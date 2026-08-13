"""
PayFast subscription checkout + ITN handling.

Two recurring plans, each its own PayFast subscription:
- Monitoring (R99/mo) — flips the user's tier to SUBSCRIBED, unlocking the
  subscriber features.
- Cylinder-swap add-on (R199/mo) — an optional extra, only offered once the
  monitoring subscription is active. When a cylinder runs empty, APS swaps it.

Flow (per plan):
1. An authenticated user hits the plan's create-checkout endpoint. We ensure a
   Subscription row exists and return the PayFast form data + URL; the frontend
   auto-submits that form to redirect the user to PayFast.
2. PayFast calls ``payfast-notify/`` (ITN) server-to-server. Both plans share
   this one endpoint; we tell them apart by the ``m_payment_id`` prefix
   ("sub-<id>" vs "swap-<id>"). On COMPLETE we record an immutable Payment row
   and update the relevant fields on the Subscription.
3. The user is redirected back to ``return_url`` / ``cancel_url`` on the
   frontend, which re-fetches /users/me/ to pick up any changes.

Signature + IP verification follow the APS / Lumo reference implementations.
"""

import hashlib
import logging
import socket
import urllib.parse
from collections import OrderedDict
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.gas_guard.users.authentication import GasGuardJWTAuthentication
from .models import Payment, Subscription

logger = logging.getLogger(__name__)

# Monthly prices, ZAR. Mirror SUB_PRICE_ZAR / SWAP_PRICE_ZAR on the frontend.
SUBSCRIPTION_AMOUNT = '99.00'
SWAP_ADDON_AMOUNT = '199.00'

# m_payment_id prefixes, so one ITN endpoint can route both plans.
MONITORING_PREFIX = 'sub-'
SWAP_PREFIX = 'swap-'


def _parse_m_payment_id(m_payment_id):
    """
    Resolve an ITN's ``m_payment_id`` to a (plan, subscription_id) pair.

    Accepts the prefixed forms ("sub-12" / "swap-12") and, for backward
    compatibility with recurring monitoring subscriptions created before the
    prefix existed, a bare numeric id (treated as monitoring). Returns
    (None, None) if it can't be parsed.
    """
    if not m_payment_id:
        return None, None
    if m_payment_id.startswith(SWAP_PREFIX):
        raw = m_payment_id[len(SWAP_PREFIX):]
        plan = Payment.Plan.CYLINDER_SWAP
    elif m_payment_id.startswith(MONITORING_PREFIX):
        raw = m_payment_id[len(MONITORING_PREFIX):]
        plan = Payment.Plan.MONITORING
    else:
        raw = m_payment_id  # legacy bare id → monitoring
        plan = Payment.Plan.MONITORING
    try:
        return plan, int(raw)
    except (TypeError, ValueError):
        return None, None


def _to_decimal(value):
    """PayFast amount string → Decimal, or None if absent/unparseable."""
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def generate_signature(data, passphrase=''):
    """
    MD5 signature for a PayFast payment request.

    Matches the working aps-website / lumo implementations exactly: sort the
    fields by key, urlencode each value (spaces as '+'), join with '&', then
    append the passphrase (if any). The submitted form must contain the same
    fields this was computed over.
    """
    payload = ''
    ordered = OrderedDict(sorted(data.items(), key=lambda kv: kv[0]))
    for key in ordered:
        payload += key + '=' + urllib.parse.quote_plus(str(ordered[key]).replace('+', ' ')) + '&'
    payload = payload[:-1]
    if passphrase != '':
        payload += f'&passphrase={passphrase}'
    return hashlib.md5(payload.encode()).hexdigest()


def _build_payfast_data(user, m_payment_id, amount, item_name, item_description):
    """
    Assemble the signed PayFast form for a monthly recurring checkout.

    Shared by both plans — only the id, amount and item copy differ. Returns
    the fully-signed ``data`` dict ready to hand to the frontend.
    """
    data = {
        # Merchant.
        'merchant_id': settings.PAYFAST_MERCHANT_ID,
        'merchant_key': settings.PAYFAST_MERCHANT_KEY,
        # Buyer (from the authenticated account).
        'name_first': user.first_name or '',
        'name_last': user.last_name or '',
        'email_address': user.email,
        # Transaction.
        'm_payment_id': m_payment_id,
        'amount': amount,
        'item_name': item_name,
        'item_description': item_description,
        'email_confirmation': '1',
        'confirmation_address': user.email,
        # Recurring billing — monthly, indefinite.
        'subscription_type': '1',
        'recurring_amount': amount,
        'frequency': '3',   # 3 = monthly
        'cycles': '0',      # 0 = indefinite
        # Return URLs.
        'return_url': settings.PAYFAST_GAS_GUARD_RETURN_URL,
        'cancel_url': settings.PAYFAST_GAS_GUARD_CANCEL_URL,
        'notify_url': settings.PAYFAST_GAS_GUARD_NOTIFY_URL,
    }

    # PayFast rejects a notify_url pointing at localhost/127.0.0.1 (it must be
    # publicly reachable), which surfaces as a "bad signature" error. When
    # testing locally without a tunnel, omit it entirely — payment still works,
    # only the ITN callback won't fire.
    notify = data.get('notify_url', '')
    if any(h in notify for h in ('localhost', '127.0.0.1', '0.0.0.0')):
        data.pop('notify_url', None)

    # Drop blank fields entirely: they must not appear in the signature NOR in
    # the submitted form, or PayFast's recompute won't match.
    data = {k: v for k, v in data.items() if v is not None and str(v).strip() != ''}
    data['signature'] = generate_signature(data, settings.PAYFAST_PASSPHRASE)
    return data


def _payfast_api_signature(headers, passphrase=''):
    """
    MD5 signature for a PayFast REST API request.

    The API (unlike the checkout form) signs the request HEADERS: sort by key,
    urlencode values, join with '&', append the passphrase. Used for the
    recurring-subscription cancel call.
    """
    ordered = OrderedDict(sorted(headers.items(), key=lambda kv: kv[0]))
    payload = '&'.join(
        f'{k}={urllib.parse.quote_plus(str(v).strip())}' for k, v in ordered.items()
    )
    if passphrase:
        payload += f'&passphrase={urllib.parse.quote_plus(passphrase.strip())}'
    return hashlib.md5(payload.encode()).hexdigest()


def cancel_payfast_subscription(token):
    """
    Cancel a recurring PayFast subscription by its token via the REST API.

    Returns True on success. In sandbox we don't have a real recurring token to
    cancel (sandbox checkouts don't always mint one), so we treat cancellation
    as a local-only state change and return True without calling PayFast. In
    production we POST to /subscriptions/{token}/cancel with a signed header set;
    any network/HTTP error is logged and surfaced as False so the caller can
    report it rather than silently marking the plan cancelled.
    """
    if not token:
        # Nothing to cancel upstream (e.g. never got a token in sandbox). Allow
        # the local deactivation to proceed.
        return True

    if settings.PAYFAST_SANDBOX:
        logger.info(f'Sandbox: skipping PayFast API cancel for token {token}')
        return True

    # PayFast API auth headers (signed). Timestamp is ISO-8601 in UTC.
    headers = {
        'merchant-id': settings.PAYFAST_MERCHANT_ID,
        'version': 'v1',
        'timestamp': datetime.now(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
    }
    headers['signature'] = _payfast_api_signature(headers, settings.PAYFAST_PASSPHRASE)

    url = f'{settings.PAYFAST_GAS_GUARD_API_URL}/subscriptions/{token}/cancel?testing=false'
    request = Request(url, data=b'', method='PUT', headers=headers)
    try:
        with urlopen(request, timeout=20) as resp:
            body = resp.read().decode('utf-8', 'replace')
            logger.info(f'PayFast cancel OK for token {token}: {body}')
            return True
    except HTTPError as e:
        detail = e.read().decode('utf-8', 'replace') if e.fp else ''
        logger.error(f'PayFast cancel HTTP {e.code} for token {token}: {detail}')
        return False
    except (URLError, OSError) as e:
        logger.error(f'PayFast cancel network error for token {token}: {e}')
        return False


class SubscriptionStatusView(APIView):
    """
    GET: the logged-in user's subscription + add-on state for the UI.

    Returns both the compact booleans the gating logic relies on and the richer
    per-plan detail (price, months paid, last payment) the plan-management page
    renders. camelCase to match the frontend convention.
    """

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def get(self, request):
        subscription = getattr(request.user, 'subscription', None)
        subscribed = bool(subscription and subscription.is_active)
        swap_active = bool(subscription and subscription.swap_addon_active)

        def _iso(dt):
            return dt.isoformat() if dt else None

        return Response({
            # Compact flags (used across the app for feature gating).
            'subscribed': subscribed,
            'swapAddonActive': swap_active,
            # Per-plan detail for the management page.
            'monitoring': {
                'active': subscribed,
                'priceZar': int(float(SUBSCRIPTION_AMOUNT)),
                'monthsPaid': subscription.subscription_length if subscription else 0,
                'lastPaymentDate': _iso(subscription.last_payment_date) if subscription else None,
            },
            'swapAddon': {
                'active': swap_active,
                'priceZar': int(float(SWAP_ADDON_AMOUNT)),
                'monthsPaid': subscription.swap_addon_length if subscription else 0,
                'lastPaymentDate': _iso(subscription.swap_addon_last_payment_date) if subscription else None,
                # The add-on can only be purchased while monitoring is active.
                'available': subscribed,
            },
        })


class PaymentHistoryView(APIView):
    """
    GET: the logged-in user's confirmed payments, newest first.

    Returns the full per-payment detail (both plans) so the frontend can let the
    user download a complete record of their payments. Sourced from the immutable
    Payment audit trail, not the mutable Subscription counters. camelCase to match
    the frontend convention.
    """

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def get(self, request):
        payments = Payment.objects.filter(user=request.user)  # Meta orders newest-first

        def _dec(value):
            return str(value) if value is not None else None

        return Response({
            'payments': [
                {
                    'date': p.created_at.isoformat(),
                    'plan': p.plan,
                    'planLabel': p.get_plan_display(),
                    'itemName': p.item_name,
                    'amountGross': _dec(p.amount_gross),
                    'amountFee': _dec(p.amount_fee),
                    'amountNet': _dec(p.amount_net),
                    'currency': 'ZAR',
                    'status': p.payment_status,
                    'pfPaymentId': p.pf_payment_id,
                    'mPaymentId': p.m_payment_id,
                }
                for p in payments
            ],
        })


@method_decorator(csrf_exempt, name='dispatch')
class CreatePayFastCheckoutSession(APIView):
    """POST: start a PayFast checkout for the R99 monitoring subscription."""

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if user.tier == user.Tier.SUBSCRIBED:
            return Response(
                {'detail': 'You already have an active subscription.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # One subscription row per user; reuse it so m_payment_id is stable.
            subscription, _ = Subscription.objects.get_or_create(user=user)
            data = _build_payfast_data(
                user,
                m_payment_id=f'{MONITORING_PREFIX}{subscription.id}',
                amount=SUBSCRIPTION_AMOUNT,
                item_name='Gas Guard Subscription',
                item_description='Gas Guard monthly subscription',
            )
            return Response({
                'url': settings.PAYFAST_GAS_GUARD_PAYMENT_URL,
                'data': data,
                'method': 'POST',
            })
        except Exception as e:
            logger.error(f'Error creating PayFast checkout session: {e}')
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@method_decorator(csrf_exempt, name='dispatch')
class CreateSwapAddonCheckoutSession(APIView):
    """
    POST: start a PayFast checkout for the R199 cylinder-swap add-on.

    Only available once the R99 monitoring subscription is active — you need
    monitoring to know when a cylinder is empty. Refuses if the add-on is
    already active.
    """

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        # Gate: the monitoring subscription must be active first.
        if user.tier != user.Tier.SUBSCRIBED:
            return Response(
                {'detail': 'The cylinder-swap add-on requires an active Gas Guard subscription.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription = getattr(user, 'subscription', None)
        if subscription is None or not subscription.is_active:
            return Response(
                {'detail': 'The cylinder-swap add-on requires an active Gas Guard subscription.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if subscription.swap_addon_active:
            return Response(
                {'detail': 'You already have the cylinder-swap add-on.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            data = _build_payfast_data(
                user,
                m_payment_id=f'{SWAP_PREFIX}{subscription.id}',
                amount=SWAP_ADDON_AMOUNT,
                item_name='Gas Guard Cylinder Swap Add-on',
                item_description='Monthly cylinder-swap service — empty cylinders replaced',
            )
            return Response({
                'url': settings.PAYFAST_GAS_GUARD_PAYMENT_URL,
                'data': data,
                'method': 'POST',
            })
        except Exception as e:
            logger.error(f'Error creating swap add-on checkout session: {e}')
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@method_decorator(csrf_exempt, name='dispatch')
class CancelSubscriptionView(APIView):
    """
    POST: cancel the R99 monitoring subscription.

    Cancels the recurring billing at PayFast (by token), deactivates the
    subscription, and drops the user back to the DEVICE tier — which also
    removes access to the subscriber features. Because the cylinder-swap add-on
    depends on an active monitoring subscription, cancelling monitoring also
    cancels the add-on (you can't keep swaps running without monitoring).
    """

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        subscription = getattr(user, 'subscription', None)
        if subscription is None or not subscription.is_active:
            return Response(
                {'detail': "You don't have an active subscription to cancel."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cancel the recurring billing upstream first; if that fails in
        # production, don't lie to the user by flipping the local state.
        if not cancel_payfast_subscription(subscription.payfast_token):
            return Response(
                {'detail': 'Could not cancel the subscription with PayFast. '
                           'Please try again shortly or contact APS.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        with transaction.atomic():
            # Cascade: no monitoring → no add-on. Cancel the add-on's billing too.
            if subscription.swap_addon_active:
                cancel_payfast_subscription(subscription.swap_addon_token)
                subscription.swap_addon_active = False
            subscription.is_active = False
            subscription.save()
            if user.tier != user.Tier.DEVICE:
                user.tier = user.Tier.DEVICE
                user.save(update_fields=['tier'])

        logger.info(f'Monitoring subscription cancelled by user {user.email}')
        return Response({'detail': 'Your subscription has been cancelled.'})


@method_decorator(csrf_exempt, name='dispatch')
class CancelSwapAddonView(APIView):
    """POST: cancel just the R199 cylinder-swap add-on, leaving monitoring active."""

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        subscription = getattr(user, 'subscription', None)
        if subscription is None or not subscription.swap_addon_active:
            return Response(
                {'detail': "You don't have the cylinder-swap add-on to cancel."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not cancel_payfast_subscription(subscription.swap_addon_token):
            return Response(
                {'detail': 'Could not cancel the add-on with PayFast. '
                           'Please try again shortly or contact APS.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        subscription.swap_addon_active = False
        subscription.save(update_fields=['swap_addon_active', 'updated_at'])
        logger.info(f'Cylinder-swap add-on cancelled by user {user.email}')
        return Response({'detail': 'The cylinder-swap add-on has been cancelled.'})


@csrf_exempt
def payfast_notify(request):
    """
    PayFast ITN (Instant Transaction Notification) handler — shared by both
    plans. Called server-to-server to confirm payment status.

    Routes by the ``m_payment_id`` prefix ("sub-" → monitoring, "swap-" →
    cylinder-swap add-on). On COMPLETE it records an immutable Payment row
    (idempotent on pf_payment_id) and updates the matching fields on the
    Subscription. The monitoring plan additionally flips the user's tier.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        post_data = request.POST.dict()
        logger.info(f'PayFast ITN received: {post_data}')

        # Verify the request really came from PayFast (skipped in sandbox — the
        # sandbox does not always originate from the published host list).
        if not settings.PAYFAST_SANDBOX and not verify_payfast_ip(request):
            logger.error('PayFast IP verification failed')
            return HttpResponse(status=401)

        m_payment_id = post_data.get('m_payment_id')
        plan, subscription_id = _parse_m_payment_id(m_payment_id)
        if plan is None or subscription_id is None:
            logger.error(f'Unparseable m_payment_id in ITN: {m_payment_id!r}')
            return HttpResponse(status=400)

        # Per-plan expected amount.
        expected_amount = (
            SWAP_ADDON_AMOUNT if plan == Payment.Plan.CYLINDER_SWAP
            else SUBSCRIPTION_AMOUNT
        )
        if post_data.get('amount_gross') != expected_amount:
            logger.error(
                f'Amount mismatch for {plan}: expected {expected_amount}, '
                f"got {post_data.get('amount_gross')}"
            )
            return HttpResponse(status=400)

        try:
            subscription = Subscription.objects.select_related('user').get(id=subscription_id)
        except Subscription.DoesNotExist:
            logger.error(f'Subscription {subscription_id} not found')
            return HttpResponse(status=404)

        payment_status = post_data.get('payment_status')

        if payment_status == 'COMPLETE':
            _handle_complete_payment(plan, subscription, post_data)
        elif payment_status == 'CANCELLED':
            _handle_cancelled_payment(plan, subscription)
        elif payment_status == 'FAILED':
            logger.warning(f'Payment FAILED for {plan} on subscription {subscription_id}')

        return HttpResponse(status=200)

    except Exception as e:
        logger.error(f'Error processing PayFast ITN: {e}')
        return HttpResponse(status=500)


@transaction.atomic
def _handle_complete_payment(plan, subscription, post_data):
    """Record the payment and apply a COMPLETE ITN to the subscription."""
    user = subscription.user
    pf_payment_id = post_data.get('pf_payment_id')

    # Idempotency: PayFast retries ITNs. If we've already recorded this exact
    # payment, don't double-count months or re-record it.
    if pf_payment_id and Payment.objects.filter(pf_payment_id=pf_payment_id).exists():
        logger.info(f'Duplicate ITN for pf_payment_id={pf_payment_id} — ignored')
        return

    Payment.objects.create(
        user=user,
        subscription=subscription,
        plan=plan,
        amount_gross=_to_decimal(post_data.get('amount_gross')),
        amount_fee=_to_decimal(post_data.get('amount_fee')),
        amount_net=_to_decimal(post_data.get('amount_net')),
        pf_payment_id=pf_payment_id or '',
        m_payment_id=post_data.get('m_payment_id', ''),
        payfast_token=post_data.get('token'),
        payment_status='COMPLETE',
        item_name=post_data.get('item_name', ''),
        raw_payload=post_data,
    )

    now = timezone.now()
    if plan == Payment.Plan.CYLINDER_SWAP:
        subscription.swap_addon_active = True
        subscription.swap_addon_token = post_data.get('token')
        subscription.swap_addon_payment_id = pf_payment_id
        subscription.swap_addon_last_payment_date = now
        subscription.swap_addon_length += 1
        subscription.save()
        logger.info(f'Cylinder-swap add-on activated for {user.email}')
    else:
        subscription.is_active = True
        subscription.payfast_token = post_data.get('token')
        subscription.payfast_payment_id = pf_payment_id
        subscription.last_payment_date = now
        subscription.subscription_length += 1
        subscription.save()
        # Unlock the subscriber tier.
        if user.tier != user.Tier.SUBSCRIBED:
            user.tier = user.Tier.SUBSCRIBED
            user.save(update_fields=['tier'])
        logger.info(f'Monitoring subscription activated for {user.email}')

    _email_admin_new_payment(subscription, plan, post_data.get('amount_gross'))


def _handle_cancelled_payment(plan, subscription):
    """Apply a CANCELLED ITN — deactivate the relevant plan."""
    if plan == Payment.Plan.CYLINDER_SWAP:
        subscription.swap_addon_active = False
        subscription.save(update_fields=['swap_addon_active', 'updated_at'])
        logger.info(f'Cylinder-swap add-on cancelled for subscription {subscription.id}')
    else:
        subscription.is_active = False
        subscription.save(update_fields=['is_active', 'updated_at'])
        logger.info(f'Monitoring subscription cancelled for subscription {subscription.id}')


@csrf_exempt
def payfast_return(request):
    """User is redirected here after a successful/attempted payment."""
    return JsonResponse({
        'status': 'success',
        'message': 'Payment processing. Your subscription will activate shortly.',
    })


@csrf_exempt
def payfast_cancel(request):
    """User is redirected here if they cancel the PayFast payment."""
    return JsonResponse({'status': 'cancelled', 'message': 'Payment was cancelled.'})


def _email_admin_new_payment(subscription, plan, amount_gross):
    """Notify the APS inbox that a payment (either plan) came through."""
    recipient = getattr(settings, 'EMAIL_RECIPIENT', None)
    sender = settings.EMAIL_HOST_USER
    if not recipient or not sender:
        return

    user = subscription.user
    name = f'{user.first_name} {user.last_name}'.strip() or user.email
    is_swap = plan == Payment.Plan.CYLINDER_SWAP
    plan_label = 'Cylinder Swap Add-on' if is_swap else 'Monitoring Subscription'
    months = subscription.swap_addon_length if is_swap else subscription.subscription_length
    amount = amount_gross or (SWAP_ADDON_AMOUNT if is_swap else SUBSCRIPTION_AMOUNT)

    html_message = f"""
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: 0 auto;
                border: 1px solid #e5e7eb; border-radius: 12px; overflow: hidden;">
      <div style="background: #0c0e12; color: #fff; padding: 20px 24px;">
        <h2 style="margin: 0; font-size: 18px;">New payment received</h2>
        <p style="margin: 4px 0 0; color: #9aa4b2; font-size: 13px;">Gas Guard &mdash; {plan_label}</p>
      </div>
      <div style="padding: 24px; font-size: 14px;">
        <table style="width: 100%; border-collapse: collapse;">
          <tr><td style="padding: 6px 0; color: #6b7280; width: 130px;">Customer</td>
              <td style="padding: 6px 0;"><strong>{name}</strong></td></tr>
          <tr><td style="padding: 6px 0; color: #6b7280;">Email</td>
              <td style="padding: 6px 0;">{user.email}</td></tr>
          <tr><td style="padding: 6px 0; color: #6b7280;">Plan</td>
              <td style="padding: 6px 0;">{plan_label}</td></tr>
          <tr><td style="padding: 6px 0; color: #6b7280;">Amount</td>
              <td style="padding: 6px 0;">R{amount}</td></tr>
          <tr><td style="padding: 6px 0; color: #6b7280;">Months paid</td>
              <td style="padding: 6px 0;">{months}</td></tr>
        </table>
      </div>
      <div style="background: #f9fafb; padding: 14px 24px; color: #9ca3af; font-size: 12px;">
        Gas Guard &mdash; a product of APS
      </div>
    </div>
    """
    try:
        email = EmailMessage(
            subject=f'Gas Guard: new {plan_label.lower()} payment from {name}',
            body=html_message,
            from_email=sender,
            to=[recipient],
        )
        email.content_subtype = 'html'
        email.send(fail_silently=True)
    except Exception as e:
        logger.error(f'Error sending payment notification email: {e}')


def verify_payfast_ip(request):
    """Verify the ITN request originates from a PayFast host (by Referer)."""
    valid_hosts = [
        'www.payfast.co.za',
        'sandbox.payfast.co.za',
        'w1w.payfast.co.za',
        'w2w.payfast.co.za',
    ]
    valid_ips = []
    for host in valid_hosts:
        try:
            ips = socket.gethostbyname_ex(host)
        except socket.gaierror:
            continue
        for entry in ips:
            if isinstance(entry, list):
                valid_ips.extend(entry)
            elif entry:
                valid_ips.append(entry)

    clean_valid_ips = list(dict.fromkeys(valid_ips))
    referer = request.headers.get('Referer', '')
    return urlparse(referer).hostname in clean_valid_ips
