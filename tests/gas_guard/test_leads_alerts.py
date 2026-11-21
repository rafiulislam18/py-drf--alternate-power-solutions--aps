"""
Tests for two smaller Gas Guard apps:

- leads: the public demo-booking endpoint (creates a DemoBooking, notifies
  admin by email — email patched / gated).
- alerts: the edge-triggered low-gas check task and its LowGasAlertState latch.
  The email delivery is patched so we assert on transitions, not SMTP.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.gas_guard.alerts.models import LowGasAlertState
from apps.gas_guard.alerts.tasks import check_low_gas
from apps.gas_guard.leads.models import DemoBooking
from apps.gas_guard.users.models import GasGuardUser
from apps.gas_guard.weight_scale.models import ScaleDevice, WeightReading

DEMO_BOOKINGS = '/gas-guard/leads/demo-bookings/'
SEND_EMAIL_PATH = 'apps.gas_guard.alerts.tasks.send_low_gas_email'


# ── leads ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_demo_booking_created_publicly(api_client):
    resp = api_client.post(
        DEMO_BOOKINGS,
        {'name': 'Jane', 'email': 'jane@example.com', 'company': 'Acme', 'sites': '12'},
        format='json',
    )
    assert resp.status_code == 201
    assert DemoBooking.objects.filter(email='jane@example.com').count() == 1


@pytest.mark.django_db
def test_demo_booking_requires_name_and_email(api_client):
    resp = api_client.post(DEMO_BOOKINGS, {'company': 'Acme'}, format='json')
    assert resp.status_code == 400
    assert DemoBooking.objects.count() == 0


@override_settings(EMAIL_RECIPIENT='sales@example.com')
@pytest.mark.django_db
def test_demo_booking_email_failure_does_not_break_request(api_client):
    # Even if the admin notification raises, the booking still succeeds (the
    # view sends with fail_silently and wraps it in try/except).
    with patch('apps.gas_guard.leads.views.EmailMessage', side_effect=Exception('smtp down')):
        resp = api_client.post(
            DEMO_BOOKINGS, {'name': 'Jane', 'email': 'jane@example.com'}, format='json'
        )
    assert resp.status_code == 201
    assert DemoBooking.objects.filter(email='jane@example.com').exists()


# ── alerts: low-gas check task ───────────────────────────────────────────────

@pytest.fixture
def owner(db):
    u = GasGuardUser(email='owner@example.com', tier=GasGuardUser.Tier.SUBSCRIBED)
    u.set_password('x')
    u.save()
    return u


def _device_with_total(owner, total_kg, *, tare='15.00', full='48.00'):
    """A device whose latest reading puts total weight at ``total_kg``."""
    dev = ScaleDevice.objects.create(
        device_id=f'dev-{total_kg}', name=f'Site-{total_kg}', owner=owner,
        tare_kg=Decimal(tare), full_gas_kg=Decimal(full),
    )
    WeightReading.objects.create(
        device=dev, weight=Decimal(str(total_kg)), unit='kg', received_at=timezone.now(),
    )
    return dev


@override_settings(GAS_GUARD_LOW_GAS_THRESHOLD_PCT=30)
@pytest.mark.django_db
@patch(SEND_EMAIL_PATH, return_value=True)
def test_low_gas_rising_edge_alerts_once(mock_email, owner):
    # total 20kg, tare 15, full 48 → gas 5kg → ~10% ≤ 30% threshold → LOW.
    dev = _device_with_total(owner, '20.0')

    check_low_gas.apply().get()

    state = LowGasAlertState.objects.get(device=dev)
    assert state.is_active is True
    assert mock_email.call_count == 1

    # Running again with no change must NOT re-alert (edge-triggered).
    check_low_gas.apply().get()
    assert mock_email.call_count == 1


@override_settings(GAS_GUARD_LOW_GAS_THRESHOLD_PCT=30)
@pytest.mark.django_db
@patch(SEND_EMAIL_PATH, return_value=True)
def test_healthy_site_does_not_alert(mock_email, owner):
    # total 45kg → gas 30kg → ~62% > 30% → not low.
    dev = _device_with_total(owner, '45.0')

    check_low_gas.apply().get()

    assert not LowGasAlertState.objects.filter(device=dev, is_active=True).exists()
    mock_email.assert_not_called()


@override_settings(GAS_GUARD_LOW_GAS_THRESHOLD_PCT=30)
@pytest.mark.django_db
@patch(SEND_EMAIL_PATH, return_value=True)
def test_recovery_edge_sends_recovery_and_clears_latch(mock_email, owner):
    dev = _device_with_total(owner, '20.0')  # low
    check_low_gas.apply().get()
    assert LowGasAlertState.objects.get(device=dev).is_active is True
    assert mock_email.call_count == 1

    # Refill: new heavier reading → healthy → recovery transition.
    WeightReading.objects.create(
        device=dev, weight=Decimal('46.0'), unit='kg', received_at=timezone.now(),
    )
    check_low_gas.apply().get()

    dev_state = LowGasAlertState.objects.get(device=dev)
    assert dev_state.is_active is False
    assert mock_email.call_count == 2  # one alert + one recovery


@override_settings(GAS_GUARD_LOW_GAS_THRESHOLD_PCT=30)
@pytest.mark.django_db
@patch(SEND_EMAIL_PATH, return_value=True)
def test_device_without_readings_is_skipped(mock_email, owner):
    ScaleDevice.objects.create(device_id='dev-empty', name='Empty', owner=owner)
    check_low_gas.apply().get()
    # No readings → no state row, no email.
    assert LowGasAlertState.objects.count() == 0
    mock_email.assert_not_called()


@pytest.mark.django_db
def test_low_gas_alert_state_str(owner):
    dev = _device_with_total(owner, '20.0')
    state = LowGasAlertState.objects.create(device=dev, is_active=True)
    assert 'LOW' in str(state)


# ── who gets the low-gas email: the owner only for a monitored site ──────────

def _recipients(mock):
    """The recipient list of each send_low_gas_email call."""
    return [c.args[1] for c in mock.call_args_list]


@override_settings(GAS_GUARD_LOW_GAS_THRESHOLD_PCT=30, EMAIL_RECIPIENT='info@aps.example')
@pytest.mark.django_db
@patch(SEND_EMAIL_PATH, return_value=True)
def test_monitored_site_emails_owner_and_office(mock_email, owner):
    from apps.gas_guard.subscription.models import Subscription
    dev = _device_with_total(owner, '20.0')
    Subscription.objects.create(user=owner, device=dev, is_active=True)
    owner.notify_email = 'alerts@owner.example'
    owner.save()
    check_low_gas.apply().get()
    assert _recipients(mock_email) == [['alerts@owner.example', 'info@aps.example']]


@override_settings(GAS_GUARD_LOW_GAS_THRESHOLD_PCT=30, EMAIL_RECIPIENT='info@aps.example')
@pytest.mark.django_db
@pytest.mark.parametrize('subscription', [None, False])  # never subscribed / cancelled
@patch(SEND_EMAIL_PATH, return_value=True)
def test_unmonitored_site_emails_the_office_only(mock_email, owner, subscription):
    from apps.gas_guard.subscription.models import Subscription
    dev = _device_with_total(owner, '20.0')
    if subscription is not None:
        Subscription.objects.create(user=owner, device=dev, is_active=subscription)
    check_low_gas.apply().get()
    assert _recipients(mock_email) == [['info@aps.example']]
    # The refill email follows the same rule.
    WeightReading.objects.create(device=dev, weight=Decimal('46.0'), unit='kg', received_at=timezone.now())
    check_low_gas.apply().get()
    assert _recipients(mock_email)[1] == ['info@aps.example']


@override_settings(GAS_GUARD_LOW_GAS_THRESHOLD_PCT=30, EMAIL_RECIPIENT='info@aps.example')
@pytest.mark.django_db
@patch(SEND_EMAIL_PATH, return_value=True)
def test_subscription_is_per_site(mock_email, owner):
    from apps.gas_guard.subscription.models import Subscription
    paid = _device_with_total(owner, '45.0')
    Subscription.objects.create(user=owner, device=paid, is_active=True)  # account tier: subscribed
    _device_with_total(owner, '20.0')  # this one isn't subscribed
    check_low_gas.apply().get()
    assert _recipients(mock_email) == [['info@aps.example']]


@pytest.mark.django_db
def test_site_is_monitored(owner):
    from apps.gas_guard.subscription.models import Subscription, site_is_monitored
    dev = _device_with_total(owner, '45.0')
    assert site_is_monitored(dev) is False
    sub = Subscription.objects.create(user=owner, device=dev, is_active=True)
    assert site_is_monitored(dev) is True
    sub.is_active = False
    sub.save()
    assert site_is_monitored(dev) is False
    assert site_is_monitored(None) is False


# ── the owner's "Low-gas email alerts" switch ────────────────────────────────

@override_settings(GAS_GUARD_LOW_GAS_THRESHOLD_PCT=30, EMAIL_RECIPIENT='info@aps.example')
@pytest.mark.django_db
@patch(SEND_EMAIL_PATH, return_value=True)
def test_switch_off_means_no_owner_email_for_any_site(mock_email, owner):
    from apps.gas_guard.subscription.models import Subscription
    owner.email_alerts_enabled = False
    owner.save()
    dev = _device_with_total(owner, '20.0')
    Subscription.objects.create(user=owner, device=dev, is_active=True)
    check_low_gas.apply().get()
    assert _recipients(mock_email) == [['info@aps.example']]  # the office still gets it
    WeightReading.objects.create(device=dev, weight=Decimal('46.0'), unit='kg', received_at=timezone.now())
    check_low_gas.apply().get()
    assert _recipients(mock_email)[1] == ['info@aps.example']  # refill email too


@pytest.mark.django_db
def test_new_accounts_start_with_email_alerts_on():
    user = GasGuardUser(email='new@example.com')
    user.set_password('x')
    user.save()
    assert GasGuardUser.objects.get(pk=user.pk).email_alerts_enabled is True


@pytest.mark.django_db
def test_becoming_a_subscriber_switches_email_alerts_on(owner):
    from apps.gas_guard.subscription.models import Subscription
    owner.tier = GasGuardUser.Tier.DEVICE
    owner.email_alerts_enabled = False
    owner.save()
    dev = _device_with_total(owner, '45.0')
    sub = Subscription.objects.create(user=owner, device=dev, is_active=True)
    sub.sync_user_tier()
    owner.refresh_from_db()
    assert (owner.tier, owner.email_alerts_enabled) == (GasGuardUser.Tier.SUBSCRIBED, True)
    # Already a subscriber who switched it off: a second site doesn't switch it back on.
    owner.email_alerts_enabled = False
    owner.save()
    dev2 = ScaleDevice.objects.create(device_id='dev-2', name='Second', owner=owner)
    Subscription.objects.create(user=owner, device=dev2, is_active=True).sync_user_tier()
    owner.refresh_from_db()
    assert owner.email_alerts_enabled is False


@pytest.mark.django_db
def test_migration_switches_it_on_for_owners_of_active_subscriptions(owner):
    import importlib
    from django.apps import apps
    from apps.gas_guard.subscription.models import Subscription
    migration = importlib.import_module('apps.gas_guard.users.migrations.0004_email_alerts_on_for_subscribers')
    paying = owner
    lapsed = GasGuardUser.objects.create(email='lapsed@example.com')
    free = GasGuardUser.objects.create(email='free@example.com')
    GasGuardUser.objects.update(email_alerts_enabled=False)  # as it was before (default off)
    Subscription.objects.create(user=paying, device=_device_with_total(paying, '45.0'), is_active=True)
    lapsed_dev = ScaleDevice.objects.create(device_id='dev-l', name='Lapsed', owner=lapsed)
    Subscription.objects.create(user=lapsed, device=lapsed_dev, is_active=False)
    ScaleDevice.objects.create(device_id='dev-f', name='Free', owner=free)
    migration.switch_on(apps, None)
    on = dict(GasGuardUser.objects.values_list('email', 'email_alerts_enabled'))
    assert on == {'owner@example.com': True, 'lapsed@example.com': False, 'free@example.com': False}
