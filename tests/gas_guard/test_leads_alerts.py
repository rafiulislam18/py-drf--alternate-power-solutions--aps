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

DEMO_BOOKINGS = '/api/gas-guard/leads/demo-bookings/'
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
