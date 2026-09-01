"""
Public "Manage Your Subscriptions" portal.

Flow:
1. ``POST request-otp/``   {email}         → neutral message; emails a 6-digit
   code IFF the email owns a subscription in either main app. Re-uses a still-
   live code rather than sending a second one.
2. ``POST verify-otp/``    {email, code}   → on success returns a 60-min scoped
   JWT (see tokens.py). Attempt-capped + single-use.
3. ``GET  subscriptions/`` (Bearer)        → the token-email's subscriptions
   across both apps, normalised.
4. ``POST cancel/``        {ref} (Bearer)  → cancels the referenced recurring
   plan at PayFast AND deactivates it in our DB, but only if the token-email
   owns it. Access is kept until period end (we only flip is_active off).

Security: the email is always taken from the signed token (never the body) for
2 protected endpoints; OTP responses are neutral to avoid customer-email
enumeration; codes are single-use, expiring and attempt-capped; both public
endpoints are throttled.
"""

import logging
import random
import string
from datetime import timedelta

from django.core.signing import BadSignature
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .emails import send_otp_email
from .models import OTP_MAX_ATTEMPTS, OTP_TTL, PortalOTP
from .payfast import cancel_payfast_subscription
from .providers import gather_payments, gather_subscriptions, get_owned_subscription
from .throttling import OtpRequestThrottle, OtpVerifyThrottle
from .tokens import PortalTokenError, email_from_auth_header, issue_portal_token

logger = logging.getLogger('apps.subscription_portal')

# One neutral line for the request-OTP step so the form can't be used to probe
# which emails are APS customers.
_NEUTRAL = 'If that email has a subscription with us, we\'ve sent an access code.'


def _new_code():
    return ''.join(random.choices(string.digits, k=6))


def _normalise_email(raw):
    return (raw or '').strip().lower()


@api_view(['POST'])
@authentication_classes([])  # public; don't let the global JWT auth 401 first
@permission_classes([AllowAny])
@throttle_classes([OtpRequestThrottle])
def request_otp(request):
    """Email a fresh OTP if the address owns a subscription — always neutral."""
    email = _normalise_email(request.data.get('email'))
    if not email:
        return Response({'detail': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)

    # Only send if this email actually owns something — but the response never
    # reveals which way this went.
    if gather_subscriptions(email):
        # Don't send a second code while one is still live (spec: reuse within
        # the 30-min window). Otherwise mint a new one.
        live = (
            PortalOTP.objects.filter(email=email, consumed=False)
            .order_by('-created_at')
            .first()
        )
        if not (live and live.is_live):
            code = _new_code()
            PortalOTP.objects.create(
                email=email,
                code=code,
                expires_at=timezone.now() + OTP_TTL,
            )
            send_otp_email(email, code, int(OTP_TTL.total_seconds() // 60))
        # If a live code exists we intentionally don't resend — the user still
        # has their earlier code.

    return Response({'detail': _NEUTRAL}, status=status.HTTP_200_OK)


@api_view(['POST'])
@authentication_classes([])  # public; don't let the global JWT auth 401 first
@permission_classes([AllowAny])
@throttle_classes([OtpVerifyThrottle])
def verify_otp(request):
    """Check the code; on success mint a 60-min scoped access token."""
    email = _normalise_email(request.data.get('email'))
    code = (request.data.get('code') or '').strip()
    if not email or not code:
        return Response(
            {'detail': 'Email and code are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    otp = (
        PortalOTP.objects.filter(email=email, consumed=False)
        .order_by('-created_at')
        .first()
    )
    if not otp or not otp.is_live:
        return Response(
            {'detail': 'That code is invalid or has expired. Request a new one.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if otp.code != code:
        # Count the miss; burn the code once the cap is hit.
        otp.attempts += 1
        otp.save(update_fields=['attempts'])
        remaining = OTP_MAX_ATTEMPTS - otp.attempts
        if remaining <= 0:
            return Response(
                {'detail': 'Too many incorrect attempts. Request a new code.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'detail': f'Incorrect code. {remaining} attempt(s) left.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Correct: single-use burn, then issue the scoped token.
    otp.consumed = True
    otp.save(update_fields=['consumed'])
    return Response(
        {'access': issue_portal_token(email)},
        status=status.HTTP_200_OK,
    )


def _authed_email(request):
    """Email from the bearer token, or raise PortalTokenError."""
    return email_from_auth_header(request.META.get('HTTP_AUTHORIZATION', ''))


@api_view(['GET'])
# Our own scoped token gates this — bypass DRF's global JWT auth so it doesn't
# 401 on our (non-simplejwt) bearer token before the view runs.
@authentication_classes([])
@permission_classes([AllowAny])
def subscriptions(request):
    """List the token-email's subscriptions across both apps."""
    try:
        email = _authed_email(request)
    except PortalTokenError as e:
        return Response({'detail': str(e)}, status=status.HTTP_401_UNAUTHORIZED)

    subs = gather_subscriptions(email)
    return Response(
        {'email': email, 'subscriptions': [s.as_dict() for s in subs]},
        status=status.HTTP_200_OK,
    )


@api_view(['GET'])
# Our own scoped token gates this — bypass DRF's global JWT auth (see above).
@authentication_classes([])
@permission_classes([AllowAny])
def payments(request):
    """
    The token-email's confirmed payment history across both apps.

    Read from the immutable Payment audit trail, so it reflects what was
    actually charged rather than the mutable Subscription counters. The
    frontend renders this and offers it as a CSV download.
    """
    try:
        email = _authed_email(request)
    except PortalTokenError as e:
        return Response({'detail': str(e)}, status=status.HTTP_401_UNAUTHORIZED)

    history = gather_payments(email)
    return Response(
        {'email': email, 'payments': [p.as_dict() for p in history]},
        status=status.HTTP_200_OK,
    )


@api_view(['POST'])
# Our own scoped token gates this — bypass DRF's global JWT auth (see above).
@authentication_classes([])
@permission_classes([AllowAny])
def cancel(request):
    """
    Cancel one recurring subscription: PayFast API cancel + local deactivate.

    Access is retained until the paid period ends — we only set ``is_active``
    False, we don't delete anything. Only the owner (token email) can cancel.
    """
    try:
        email = _authed_email(request)
    except PortalTokenError as e:
        return Response({'detail': str(e)}, status=status.HTTP_401_UNAUTHORIZED)

    ref = (request.data.get('ref') or '').strip()
    if not ref:
        return Response({'detail': 'Missing subscription reference.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        sub = get_owned_subscription(email, ref)
    except (BadSignature, ValueError):
        return Response({'detail': 'Invalid subscription reference.'}, status=status.HTTP_400_BAD_REQUEST)

    if sub is None:
        # Either the row is gone or it isn't owned by this email. Same 403 either
        # way so we don't leak which.
        return Response(
            {'detail': 'You are not authorised to cancel this subscription.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not sub.is_active:
        return Response(
            {'detail': 'This subscription is already inactive.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Real cancel at PayFast first — if that fails, don't touch our DB so the
    # user (and PayFast) stay consistent and they can retry.
    if not cancel_payfast_subscription(sub.payfast_token):
        return Response(
            {'detail': 'We couldn\'t cancel this subscription with the payment '
                       'provider right now. Please try again shortly.'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    with transaction.atomic():
        sub.is_active = False
        sub.save(update_fields=['is_active', 'updated_at'])

    logger.info(f'Portal cancel: {email} cancelled {ref}')
    return Response(
        {'detail': 'Your subscription has been cancelled. You keep access until '
                   'the end of your current paid period.'},
        status=status.HTTP_200_OK,
    )
