"""
Tests for the Gas Guard subscription app: status, payment history, checkout
gating, cancel (monitoring + swap add-on), and the PayFast ITN handler
(COMPLETE / CANCELLED / duplicate / amount-mismatch). PayFast network calls and
admin-email side effects are patched out.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.gas_guard.subscription.models import Payment, Subscription
from apps.gas_guard.users.models import GasGuardUser

from .conftest import auth_client_for

STATUS = '/api/gas-guard/subscription/status/'
PAYMENTS = '/api/gas-guard/subscription/payments/'
CHECKOUT = '/api/gas-guard/subscription/create-payfast-checkout/'
SWAP_CHECKOUT = '/api/gas-guard/subscription/create-swap-addon-checkout/'
CANCEL = '/api/gas-guard/subscription/cancel/'
CANCEL_SWAP = '/api/gas-guard/subscription/cancel-swap-addon/'
NOTIFY = '/api/gas-guard/subscription/payfast-notify/'

CANCEL_PATH = 'apps.gas_guard.subscription.views.cancel_payfast_subscription'
EMAIL_ADMIN_PATH = 'apps.gas_guard.subscription.views._email_admin_new_payment'

# Minimal PayFast settings so checkout/data building doesn't AttributeError.
PAYFAST_TEST_SETTINGS = dict(
    PAYFAST_SANDBOX=True,
    PAYFAST_MERCHANT_ID='10000100',
    PAYFAST_MERCHANT_KEY='46f0cd694581a',
    PAYFAST_PASSPHRASE='',
    PAYFAST_GAS_GUARD_PAYMENT_URL='https://sandbox.payfast.co.za/eng/process',
    PAYFAST_GAS_GUARD_RETURN_URL='https://x/return',
    PAYFAST_GAS_GUARD_CANCEL_URL='https://x/cancel',
    PAYFAST_GAS_GUARD_NOTIFY_URL='https://x/notify',
    PAYFAST_GAS_GUARD_API_URL='https://api.payfast.co.za',
)


@pytest.fixture
def subscribed_user(db):
    user = GasGuardUser(email='sub@example.com', tier=GasGuardUser.Tier.SUBSCRIBED)
    user.set_password('str0ng-pass-123')
    user.save()
    return user


# ── status ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_status_requires_auth(api_client):
    assert api_client.get(STATUS).status_code == 401


@pytest.mark.django_db
def test_status_device_user_not_subscribed(gg_client):
    resp = gg_client.get(STATUS)
    assert resp.status_code == 200
    assert resp.data['subscribed'] is False
    assert resp.data['swapAddonActive'] is False


@pytest.mark.django_db
def test_status_reflects_active_subscription(subscribed_user):
    Subscription.objects.create(user=subscribed_user, is_active=True, subscription_length=3)
    resp = auth_client_for(subscribed_user).get(STATUS)
    assert resp.status_code == 200
    assert resp.data['subscribed'] is True
    assert resp.data['monitoring']['monthsPaid'] == 3


# ── payment history ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_payments_lists_only_own(subscribed_user, gg_user):
    sub = Subscription.objects.create(user=subscribed_user, is_active=True)
    Payment.objects.create(
        user=subscribed_user, subscription=sub, plan=Payment.Plan.MONITORING,
        amount_gross=Decimal('99.00'), pf_payment_id='pf-1', payment_status='COMPLETE',
    )
    resp = auth_client_for(subscribed_user).get(PAYMENTS)
    assert resp.status_code == 200
    assert len(resp.data['payments']) == 1
    # A different user sees none of them.
    assert auth_client_for(gg_user).get(PAYMENTS).data['payments'] == []


# ── checkout gating ──────────────────────────────────────────────────────────

@override_settings(**PAYFAST_TEST_SETTINGS)
@pytest.mark.django_db
def test_checkout_creates_subscription_row_for_device_user(gg_client, gg_user):
    resp = gg_client.post(CHECKOUT, {}, format='json')
    assert resp.status_code == 200
    assert 'url' in resp.data and 'data' in resp.data
    assert Subscription.objects.filter(user=gg_user).exists()


@override_settings(**PAYFAST_TEST_SETTINGS)
@pytest.mark.django_db
def test_checkout_refused_when_already_subscribed(subscribed_user):
    resp = auth_client_for(subscribed_user).post(CHECKOUT, {}, format='json')
    assert resp.status_code == 400


@override_settings(**PAYFAST_TEST_SETTINGS)
@pytest.mark.django_db
def test_swap_checkout_requires_active_monitoring(gg_client):
    # Device-tier user can't buy the add-on.
    resp = gg_client.post(SWAP_CHECKOUT, {}, format='json')
    assert resp.status_code == 400


# ── cancel (monitoring) ──────────────────────────────────────────────────────

@pytest.mark.django_db
@patch(CANCEL_PATH, return_value=True)
def test_cancel_monitoring_deactivates_and_downgrades(mock_cancel, subscribed_user):
    Subscription.objects.create(
        user=subscribed_user, is_active=True, payfast_token='tok-1', subscription_length=2,
    )
    resp = auth_client_for(subscribed_user).post(CANCEL, {}, format='json')
    assert resp.status_code == 200
    mock_cancel.assert_called_once_with('tok-1')
    subscribed_user.refresh_from_db()
    assert subscribed_user.tier == GasGuardUser.Tier.DEVICE
    assert subscribed_user.subscription.is_active is False


@pytest.mark.django_db
@patch(CANCEL_PATH, return_value=True)
def test_cancel_monitoring_cascades_to_addon(mock_cancel, subscribed_user):
    Subscription.objects.create(
        user=subscribed_user, is_active=True, payfast_token='tok-1',
        swap_addon_active=True, swap_addon_token='tok-swap',
    )
    resp = auth_client_for(subscribed_user).post(CANCEL, {}, format='json')
    assert resp.status_code == 200
    subscribed_user.refresh_from_db()
    assert subscribed_user.subscription.swap_addon_active is False
    # Both plans cancelled upstream.
    assert mock_cancel.call_count == 2


@pytest.mark.django_db
def test_cancel_without_subscription_is_400(subscribed_user):
    resp = auth_client_for(subscribed_user).post(CANCEL, {}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
@patch(CANCEL_PATH, return_value=False)
def test_cancel_monitoring_payfast_failure_is_502(mock_cancel, subscribed_user):
    Subscription.objects.create(user=subscribed_user, is_active=True, payfast_token='tok-1')
    resp = auth_client_for(subscribed_user).post(CANCEL, {}, format='json')
    assert resp.status_code == 502
    subscribed_user.refresh_from_db()
    assert subscribed_user.subscription.is_active is True  # untouched on failure


# ── cancel (swap add-on) ─────────────────────────────────────────────────────

@pytest.mark.django_db
@patch(CANCEL_PATH, return_value=True)
def test_cancel_swap_addon_leaves_monitoring_active(mock_cancel, subscribed_user):
    Subscription.objects.create(
        user=subscribed_user, is_active=True, payfast_token='tok-1',
        swap_addon_active=True, swap_addon_token='tok-swap',
    )
    resp = auth_client_for(subscribed_user).post(CANCEL_SWAP, {}, format='json')
    assert resp.status_code == 200
    mock_cancel.assert_called_once_with('tok-swap')
    subscribed_user.refresh_from_db()
    assert subscribed_user.subscription.swap_addon_active is False
    assert subscribed_user.subscription.is_active is True


@pytest.mark.django_db
def test_cancel_swap_addon_without_addon_is_400(subscribed_user):
    Subscription.objects.create(user=subscribed_user, is_active=True)
    resp = auth_client_for(subscribed_user).post(CANCEL_SWAP, {}, format='json')
    assert resp.status_code == 400


# ── PayFast ITN handler ──────────────────────────────────────────────────────

def _itn(subscription_id, *, prefix='sub-', amount='99.00', status='COMPLETE', pf_id='pf-100'):
    return {
        'm_payment_id': f'{prefix}{subscription_id}',
        'pf_payment_id': pf_id,
        'payment_status': status,
        'amount_gross': amount,
        'amount_fee': '2.00',
        'amount_net': '97.00',
        'token': 'recurring-tok',
        'item_name': 'Gas Guard Subscription',
    }


@override_settings(**PAYFAST_TEST_SETTINGS)
@pytest.mark.django_db
@patch(EMAIL_ADMIN_PATH)
def test_itn_complete_activates_monitoring(mock_email, api_client, gg_user):
    sub = Subscription.objects.create(user=gg_user)
    resp = api_client.post(NOTIFY, _itn(sub.id), format='multipart')
    assert resp.status_code == 200
    sub.refresh_from_db()
    gg_user.refresh_from_db()
    assert sub.is_active is True
    assert sub.subscription_length == 1
    assert gg_user.tier == GasGuardUser.Tier.SUBSCRIBED
    assert Payment.objects.filter(pf_payment_id='pf-100').count() == 1


@override_settings(**PAYFAST_TEST_SETTINGS)
@pytest.mark.django_db
@patch(EMAIL_ADMIN_PATH)
def test_itn_is_idempotent_on_pf_payment_id(mock_email, api_client, gg_user):
    sub = Subscription.objects.create(user=gg_user)
    api_client.post(NOTIFY, _itn(sub.id), format='multipart')
    api_client.post(NOTIFY, _itn(sub.id), format='multipart')  # retry
    sub.refresh_from_db()
    assert sub.subscription_length == 1  # not double-counted
    assert Payment.objects.filter(pf_payment_id='pf-100').count() == 1


@override_settings(**PAYFAST_TEST_SETTINGS)
@pytest.mark.django_db
def test_itn_amount_mismatch_rejected(api_client, gg_user):
    sub = Subscription.objects.create(user=gg_user)
    resp = api_client.post(NOTIFY, _itn(sub.id, amount='1.00'), format='multipart')
    assert resp.status_code == 400
    sub.refresh_from_db()
    assert sub.is_active is False


@override_settings(**PAYFAST_TEST_SETTINGS)
@pytest.mark.django_db
def test_itn_unknown_subscription_404(api_client):
    resp = api_client.post(NOTIFY, _itn(99999), format='multipart')
    assert resp.status_code == 404


@override_settings(**PAYFAST_TEST_SETTINGS)
@pytest.mark.django_db
def test_itn_cancelled_deactivates(api_client, gg_user):
    sub = Subscription.objects.create(user=gg_user, is_active=True, subscription_length=1)
    resp = api_client.post(NOTIFY, _itn(sub.id, status='CANCELLED'), format='multipart')
    assert resp.status_code == 200
    sub.refresh_from_db()
    assert sub.is_active is False


@override_settings(**PAYFAST_TEST_SETTINGS)
@pytest.mark.django_db
@patch(EMAIL_ADMIN_PATH)
def test_itn_swap_addon_complete_activates_addon(mock_email, api_client, subscribed_user):
    sub = Subscription.objects.create(user=subscribed_user, is_active=True)
    resp = api_client.post(
        NOTIFY, _itn(sub.id, prefix='swap-', amount='199.00', pf_id='pf-swap'),
        format='multipart',
    )
    assert resp.status_code == 200
    sub.refresh_from_db()
    assert sub.swap_addon_active is True
    assert sub.swap_addon_length == 1


@pytest.mark.django_db
def test_itn_rejects_get(api_client):
    assert api_client.get(NOTIFY).status_code == 405
