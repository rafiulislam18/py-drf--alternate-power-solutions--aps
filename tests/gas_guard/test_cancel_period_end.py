"""
Cancelling keeps the plan (and its features, e.g. the owner's low-gas alerts)
until the month already paid for ends (apps.gas_guard.subscription.lifecycle):
paid until = last COMPLETE payment + 1 month. The hourly end_due switches it off
then. Applies to cancelling in Plans and to a CANCELLED ITN, for monitoring and
the cylinder-swap add-on. PayFast calls are mocked.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from dateutil.relativedelta import relativedelta
from django.test import override_settings
from django.utils import timezone

from apps.gas_guard.subscription import lifecycle
from apps.gas_guard.subscription.models import Subscription, site_is_monitored
from apps.gas_guard.subscription.tasks import end_due_subscriptions
from apps.gas_guard.users.models import GasGuardUser
from apps.gas_guard.weight_scale.models import ScaleDevice

from .conftest import auth_client_for
from .test_subscription import CANCEL, CANCEL_PATH, CANCEL_SWAP, CHECKOUT, NOTIFY, PAYFAST_TEST_SETTINGS, STATUS, _itn

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def owner(db):
    user = GasGuardUser(email='sub@example.com', tier=GasGuardUser.Tier.SUBSCRIBED)
    user.set_password('str0ng-pass-123')
    user.save()
    return user


@pytest.fixture
def site(owner):
    return ScaleDevice.objects.create(device_id='dev-001', name='Kitchen', owner=owner)


def paid_sub(owner, site, days_ago=10, swap_days_ago=None):
    now = timezone.now()
    return Subscription.objects.create(
        user=owner, device=site, is_active=True, subscription_length=1,
        last_payment_date=now - timedelta(days=days_ago), payfast_token='tok',
        swap_addon_active=swap_days_ago is not None, swap_addon_token='swap-tok' if swap_days_ago is not None else None,
        swap_addon_last_payment_date=now - timedelta(days=swap_days_ago) if swap_days_ago is not None else None,
    )


# ── paid_until ────────────────────────────────────────────────────────────────

def test_paid_until_is_one_calendar_month_after_the_payment():
    from datetime import datetime, timezone as tz
    assert lifecycle.paid_until(datetime(2026, 11, 21, 9, tzinfo=tz.utc)) == datetime(2026, 12, 21, 9, tzinfo=tz.utc)
    assert lifecycle.paid_until(datetime(2026, 1, 31, tzinfo=tz.utc)) == datetime(2026, 2, 28, tzinfo=tz.utc)
    assert lifecycle.paid_until(None) is None


# ── cancelling in Plans ─────────────────────────────────────────────────────

@override_settings(**PAYFAST_TEST_SETTINGS)
@patch(CANCEL_PATH, return_value=True)
def test_cancel_keeps_it_active_until_the_paid_month_ends(mock_cancel, owner, site):
    sub = paid_sub(owner, site, days_ago=10)
    res = auth_client_for(owner).post(CANCEL, {'deviceId': site.id}, format='json')
    assert res.status_code == 200
    mock_cancel.assert_called_once_with('tok')  # billing stops now
    sub.refresh_from_db()
    owner.refresh_from_db()
    expected = sub.last_payment_date + relativedelta(months=1)
    assert sub.is_active is True and sub.ends_at == expected
    assert owner.tier == GasGuardUser.Tier.SUBSCRIBED
    assert site_is_monitored(site) is True  # alerts and features still on
    assert 'stays active until' in res.data['detail']
    assert res.data['endsAt'] == expected.isoformat()


@override_settings(**PAYFAST_TEST_SETTINGS)
@patch(CANCEL_PATH, return_value=True)
def test_paid_period_already_over_ends_now(mock_cancel, owner, site):
    sub = paid_sub(owner, site, days_ago=40)  # a month's payment that ran out (failed renewals)
    auth_client_for(owner).post(CANCEL, {'deviceId': site.id}, format='json')
    sub.refresh_from_db()
    owner.refresh_from_db()
    assert sub.is_active is False and sub.ends_at is None
    assert owner.tier == GasGuardUser.Tier.DEVICE


@override_settings(**PAYFAST_TEST_SETTINGS)
@patch(CANCEL_PATH, return_value=True)
def test_no_payment_on_record_ends_now(mock_cancel, owner, site):
    sub = Subscription.objects.create(user=owner, device=site, is_active=True)  # e.g. switched on in the admin
    auth_client_for(owner).post(CANCEL, {'deviceId': site.id}, format='json')
    sub.refresh_from_db()
    assert sub.is_active is False


@override_settings(**PAYFAST_TEST_SETTINGS)
@patch(CANCEL_PATH, return_value=True)
def test_cancelling_twice_is_refused(mock_cancel, owner, site):
    paid_sub(owner, site)
    api = auth_client_for(owner)
    api.post(CANCEL, {'deviceId': site.id}, format='json')
    res = api.post(CANCEL, {'deviceId': site.id}, format='json')
    assert res.status_code == 400 and 'already cancelled' in res.data['detail']
    assert mock_cancel.call_count == 1


@override_settings(**PAYFAST_TEST_SETTINGS)
@patch(CANCEL_PATH, return_value=True)
def test_cascade_add_on_ends_at_its_own_date_never_after_monitoring(mock_cancel, owner, site):
    sub = paid_sub(owner, site, days_ago=10, swap_days_ago=20)  # add-on paid earlier
    auth_client_for(owner).post(CANCEL, {'deviceId': site.id}, format='json')
    sub.refresh_from_db()
    assert {c.args[0] for c in mock_cancel.call_args_list} == {'tok', 'swap-tok'}
    assert sub.swap_addon_active is True
    assert sub.swap_addon_ends_at == sub.swap_addon_last_payment_date + relativedelta(months=1)
    assert sub.swap_addon_ends_at < sub.ends_at

    other = ScaleDevice.objects.create(device_id='dev-002', name='Office', owner=owner)
    sub2 = paid_sub(owner, other, days_ago=20, swap_days_ago=2)  # add-on paid later than monitoring
    auth_client_for(owner).post(CANCEL, {'deviceId': other.id}, format='json')
    sub2.refresh_from_db()
    assert sub2.swap_addon_ends_at == sub2.ends_at  # capped at monitoring's end


@override_settings(**PAYFAST_TEST_SETTINGS)
@patch(CANCEL_PATH, return_value=True)
def test_remove_add_on_keeps_it_until_its_paid_month_ends(mock_cancel, owner, site):
    sub = paid_sub(owner, site, days_ago=10, swap_days_ago=5)
    res = auth_client_for(owner).post(CANCEL_SWAP, {'deviceId': site.id}, format='json')
    assert res.status_code == 200 and 'stays active until' in res.data['detail']
    sub.refresh_from_db()
    assert sub.swap_addon_active is True and sub.is_active is True and sub.ends_at is None
    assert sub.swap_addon_ends_at == sub.swap_addon_last_payment_date + relativedelta(months=1)


# ── the hourly job ──────────────────────────────────────────────────────────

@override_settings(**PAYFAST_TEST_SETTINGS)
@patch(CANCEL_PATH, return_value=True)
def test_end_due_switches_off_once_the_period_is_over(mock_cancel, owner, site):
    sub = paid_sub(owner, site, days_ago=10, swap_days_ago=10)
    auth_client_for(owner).post(CANCEL, {'deviceId': site.id}, format='json')
    sub.refresh_from_db()
    assert lifecycle.end_due(now=sub.ends_at - timedelta(minutes=1)) == {'monitoring': 0, 'swap': 0}
    assert lifecycle.end_due(now=sub.ends_at + timedelta(minutes=1))['monitoring'] == 1
    sub.refresh_from_db()
    owner.refresh_from_db()
    assert (sub.is_active, sub.swap_addon_active, sub.ends_at) == (False, False, None)
    assert owner.tier == GasGuardUser.Tier.DEVICE
    assert site_is_monitored(site) is False


def test_task_runs_end_due(owner, site):
    sub = paid_sub(owner, site)
    Subscription.objects.filter(pk=sub.pk).update(ends_at=timezone.now() - timedelta(minutes=5))
    assert end_due_subscriptions.apply().get() == '1 monitoring and 0 add-on plan(s) ended'


# ── PayFast's own notifications ─────────────────────────────────────────────

@override_settings(**PAYFAST_TEST_SETTINGS)
def test_cancelled_itn_keeps_it_until_the_paid_month_ends(api_client, owner, site):
    sub = paid_sub(owner, site, days_ago=3)
    assert api_client.post(NOTIFY, _itn(sub.id, status='CANCELLED'), format='multipart').status_code == 200
    sub.refresh_from_db()
    assert sub.is_active is True and sub.ends_at == sub.last_payment_date + relativedelta(months=1)


@override_settings(**PAYFAST_TEST_SETTINGS)
def test_cancelled_itn_after_the_period_ends_now(api_client, owner, site):
    sub = paid_sub(owner, site, days_ago=35)  # PayFast gave up after failed payments
    api_client.post(NOTIFY, _itn(sub.id, status='CANCELLED'), format='multipart')
    sub.refresh_from_db()
    assert sub.is_active is False


@override_settings(**PAYFAST_TEST_SETTINGS)
def test_cancelled_itn_for_the_add_on(api_client, owner, site):
    sub = paid_sub(owner, site, swap_days_ago=4)
    api_client.post(NOTIFY, _itn(sub.id, prefix='swap-', amount='199.00', status='CANCELLED'), format='multipart')
    sub.refresh_from_db()
    assert sub.swap_addon_active is True and sub.swap_addon_ends_at is not None


@override_settings(**PAYFAST_TEST_SETTINGS)
@patch('apps.gas_guard.subscription.views._email_admin_new_payment')
def test_a_new_payment_cancels_the_end(mock_email, api_client, owner, site):
    sub = paid_sub(owner, site)
    Subscription.objects.filter(pk=sub.pk).update(ends_at=timezone.now() + timedelta(days=5))
    api_client.post(NOTIFY, _itn(sub.id, pf_id='pf-new'), format='multipart')
    sub.refresh_from_db()
    assert sub.is_active is True and sub.ends_at is None


# ── what the Plans page sees ────────────────────────────────────────────────

@override_settings(**PAYFAST_TEST_SETTINGS)
@patch(CANCEL_PATH, return_value=True)
def test_status_shows_the_end_date_and_next_month_total(mock_cancel, owner, site):
    other = ScaleDevice.objects.create(device_id='dev-002', name='Office', owner=owner)
    paid_sub(owner, site)
    paid_sub(owner, other)
    api = auth_client_for(owner)
    api.post(CANCEL, {'deviceId': site.id}, format='json')
    data = api.get(STATUS).data
    rows = {s['name']: s for s in data['sites']}
    assert rows['Kitchen']['monitoring']['active'] is True
    assert rows['Kitchen']['monitoring']['endsAt'] is not None
    assert rows['Kitchen']['swapAddon']['available'] is False  # no new add-on on a site that's ending
    assert rows['Office']['monitoring']['endsAt'] is None
    assert data['summary']['subscribedSites'] == 2
    assert data['summary']['monthlyTotalZar'] == 99  # only Office is billed next month


@override_settings(**PAYFAST_TEST_SETTINGS)
@patch(CANCEL_PATH, return_value=True)
def test_resubscribing_waits_until_it_has_ended(mock_cancel, owner, site):
    paid_sub(owner, site)
    api = auth_client_for(owner)
    api.post(CANCEL, {'deviceId': site.id}, format='json')
    res = api.post(CHECKOUT, {'deviceId': site.id}, format='json')
    assert res.status_code == 400 and 'subscribe again after that' in res.data['detail']
