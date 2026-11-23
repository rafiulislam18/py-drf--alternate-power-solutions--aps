"""
Re-subscribing a cancelled plan that's still running (until its paid month
ends): a new PayFast subscription through the normal checkout with amount R0
and billing_date = the old plan's end date (first charge then: no double
charge, no gap). The R0 sign-up ITN keeps the site going on the new token;
later charges are ordinary months. Monitoring and the cylinder-swap add-on.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.gas_guard.subscription import lifecycle
from apps.gas_guard.subscription.models import Payment, Subscription, site_is_monitored
from apps.gas_guard.users.models import GasGuardUser
from apps.gas_guard.weight_scale.models import ScaleDevice

from .conftest import auth_client_for
from .test_subscription import CANCEL, CANCEL_PATH, CANCEL_SWAP, NOTIFY, PAYFAST_TEST_SETTINGS, STATUS, _itn

pytestmark = pytest.mark.django_db
RESUB = '/gas-guard/subscription/resubscribe-checkout/'
RESUB_SWAP = '/gas-guard/subscription/resubscribe-swap-addon-checkout/'
EMAIL_ADMIN_PATH = 'apps.gas_guard.subscription.views._email_admin_new_payment'


@pytest.fixture(autouse=True)
def payfast(settings):
    for k, v in PAYFAST_TEST_SETTINGS.items():
        setattr(settings, k, v)


@pytest.fixture
def owner(db):
    user = GasGuardUser(email='sub@example.com', tier=GasGuardUser.Tier.SUBSCRIBED)
    user.set_password('str0ng-pass-123')
    user.save()
    return user


@pytest.fixture
def site(owner):
    return ScaleDevice.objects.create(device_id='dev-001', name='Kitchen', owner=owner)


@pytest.fixture
def cancelled(owner, site):
    """Monitoring + add-on paid 10 days ago, then cancelled in Plans."""
    now = timezone.now()
    sub = Subscription.objects.create(
        user=owner, device=site, is_active=True, subscription_length=3, payfast_token='old-tok',
        last_payment_date=now - timedelta(days=10), swap_addon_active=True, swap_addon_token='old-swap',
        swap_addon_length=2, swap_addon_last_payment_date=now - timedelta(days=10),
    )
    with patch(CANCEL_PATH, return_value=True):
        auth_client_for(owner).post(CANCEL, {'deviceId': site.id}, format='json')
    sub.refresh_from_db()
    assert sub.ends_at and sub.swap_addon_ends_at
    return sub


def resub_itn(sub, *, prefix='resub-', amount='0.00', status='COMPLETE', pf_id='pf-resub', token='new-tok'):
    return {**_itn(sub.id, prefix=prefix, amount=amount, status=status, pf_id=pf_id), 'token': token}


# ── the checkout ────────────────────────────────────────────────────────────

def test_checkout_charges_nothing_now_and_bills_from_the_end_date(owner, site, cancelled):
    res = auth_client_for(owner).post(RESUB, {'deviceId': site.id}, format='json')
    assert res.status_code == 200, res.data
    data = res.data['data']
    end_day = timezone.localdate(cancelled.ends_at)
    assert data['amount'] == '0.00'
    assert data['recurring_amount'] == '99.00'
    assert data['billing_date'] == end_day.isoformat()
    assert (data['subscription_type'], data['frequency'], data['cycles']) == ('1', '3', '0')
    assert data['m_payment_id'] == f'resub-{cancelled.id}'
    assert 'signature' in data
    assert res.data['firstCharge'] == end_day.isoformat()


def test_swap_checkout_needs_monitoring_continuing_first(owner, site, cancelled):
    api = auth_client_for(owner)
    res = api.post(RESUB_SWAP, {'deviceId': site.id}, format='json')
    assert res.status_code == 400 and 'monitoring' in res.data['detail']
    Subscription.objects.filter(pk=cancelled.pk).update(ends_at=None)  # monitoring re-subscribed
    res = api.post(RESUB_SWAP, {'deviceId': site.id}, format='json')
    assert res.status_code == 200
    assert res.data['data']['amount'] == '0.00'
    assert res.data['data']['recurring_amount'] == '199.00'
    assert res.data['data']['billing_date'] == timezone.localdate(cancelled.swap_addon_ends_at).isoformat()
    assert res.data['data']['m_payment_id'] == f'reswap-{cancelled.id}'


def test_only_for_a_cancelled_plan_that_hasnt_ended(owner, site):
    sub = Subscription.objects.create(user=owner, device=site, is_active=True,
                                      last_payment_date=timezone.now() - timedelta(days=3))
    api = auth_client_for(owner)
    assert api.post(RESUB, {'deviceId': site.id}, format='json').status_code == 400  # not cancelled
    Subscription.objects.filter(pk=sub.pk).update(ends_at=timezone.now() - timedelta(minutes=1))
    res = api.post(RESUB, {'deviceId': site.id}, format='json')
    assert res.status_code == 400 and 'has ended' in res.data['detail']


def test_another_owners_site_is_refused(owner, site, cancelled):
    other = GasGuardUser.objects.create(email='other@example.com')
    assert auth_client_for(other).post(RESUB, {'deviceId': site.id}, format='json').status_code in (400, 404)


# ── PayFast confirms the new subscription ───────────────────────────────────

def test_r0_signup_keeps_the_plan_going(api_client, owner, site, cancelled):
    last_payment, months = cancelled.last_payment_date, cancelled.subscription_length
    res = api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')
    assert res.status_code == 200
    cancelled.refresh_from_db()
    assert cancelled.is_active is True and cancelled.ends_at is None
    assert cancelled.payfast_token == 'new-tok'
    # Not a paid month: nothing moves.
    assert (cancelled.last_payment_date, cancelled.subscription_length) == (last_payment, months)
    pay = Payment.objects.get(pf_payment_id='pf-resub')
    assert pay.amount_gross == 0 and pay.plan == Payment.Plan.MONITORING
    # The hourly job leaves it alone now.
    assert lifecycle.end_due(now=timezone.now() + timedelta(days=40))['monitoring'] == 0
    assert site_is_monitored(site) is True


def test_first_charge_on_the_end_date_is_an_ordinary_month(api_client, owner, site, cancelled):
    api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')
    with patch(EMAIL_ADMIN_PATH):
        res = api_client.post(NOTIFY, resub_itn(cancelled, amount='99.00', pf_id='pf-first'), format='multipart')
    assert res.status_code == 200
    cancelled.refresh_from_db()
    assert cancelled.subscription_length == 4
    assert cancelled.last_payment_date > timezone.now() - timedelta(minutes=1)
    assert cancelled.ends_at is None


def test_wrong_amount_still_refused(api_client, cancelled):
    res = api_client.post(NOTIFY, resub_itn(cancelled, amount='50.00'), format='multipart')
    assert res.status_code == 400
    cancelled.refresh_from_db()
    assert cancelled.ends_at is not None


def test_r0_signup_is_idempotent(api_client, cancelled):
    api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')
    api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')
    assert Payment.objects.filter(pf_payment_id='pf-resub').count() == 1


def test_signup_arriving_after_the_end_switches_it_back_on(api_client, owner, site, cancelled):
    lifecycle.end_due(now=cancelled.ends_at + timedelta(minutes=1))
    cancelled.refresh_from_db()
    owner.refresh_from_db()
    assert cancelled.is_active is False and owner.tier == GasGuardUser.Tier.DEVICE
    api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')
    cancelled.refresh_from_db()
    owner.refresh_from_db()
    assert cancelled.is_active is True and owner.tier == GasGuardUser.Tier.SUBSCRIBED


def test_old_subscriptions_cancelled_itn_is_ignored(api_client, cancelled):
    api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')
    old = {**_itn(cancelled.id, status='CANCELLED', pf_id='pf-old-cancel'), 'token': 'old-tok'}
    api_client.post(NOTIFY, old, format='multipart')
    cancelled.refresh_from_db()
    assert cancelled.is_active is True and cancelled.ends_at is None


def test_new_subscription_cancelled_later_ends_normally(api_client, cancelled):
    api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')
    new = {**_itn(cancelled.id, prefix='resub-', status='CANCELLED', pf_id='pf-new-cancel'), 'token': 'new-tok'}
    api_client.post(NOTIFY, new, format='multipart')
    cancelled.refresh_from_db()
    assert cancelled.ends_at is not None  # until the old paid month ends (no new payment yet)


def test_add_on_resubscribe(api_client, owner, site, cancelled):
    api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')  # monitoring first
    swap_months = cancelled.swap_addon_length
    res = api_client.post(NOTIFY, resub_itn(cancelled, prefix='reswap-', pf_id='pf-reswap', token='new-swap'),
                          format='multipart')
    assert res.status_code == 200
    cancelled.refresh_from_db()
    assert cancelled.swap_addon_active is True and cancelled.swap_addon_ends_at is None
    assert cancelled.swap_addon_token == 'new-swap'
    assert cancelled.swap_addon_length == swap_months
    assert lifecycle.end_due(now=timezone.now() + timedelta(days=40))['swap'] == 0


def test_status_after_resubscribing(api_client, owner, site, cancelled):
    api = auth_client_for(owner)
    assert api.get(STATUS).data['summary']['monthlyTotalZar'] == 0  # both cancelled: nothing next month
    api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')
    row = api.get(STATUS).data['sites'][0]
    assert row['monitoring']['endsAt'] is None and row['monitoring']['active'] is True
    assert row['swapAddon']['endsAt'] is not None  # the add-on is still on its way out
    assert api.get(STATUS).data['summary']['monthlyTotalZar'] == 99


def test_cancel_then_resubscribe_then_cancel_again(api_client, owner, site, cancelled):
    api_client.post(NOTIFY, resub_itn(cancelled), format='multipart')
    with patch(CANCEL_PATH, return_value=True) as mock_cancel:
        res = auth_client_for(owner).post(CANCEL, {'deviceId': site.id}, format='json')
    assert res.status_code == 200
    mock_cancel.assert_any_call('new-tok')  # the new PayFast subscription is the one cancelled
    cancelled.refresh_from_db()
    assert cancelled.ends_at is not None
