"""
Tests for the Gas Guard weight_scale app: device-authenticated reading ingest,
staff-gated device/reading lists, owner-scoped sites, the subscriber gate on
consumption history, and the site_payload / daily_consumption services.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.gas_guard.users.models import GasGuardUser
from apps.gas_guard.weight_scale.models import ScaleDevice, WeightReading
from apps.gas_guard.weight_scale.services import site_payload, daily_consumption, to_kg

from .conftest import auth_client_for

INGEST = '/api/gas-guard/weight-scale/readings/'
READING_LIST = '/api/gas-guard/weight-scale/readings/list/'
DEVICE_LIST = '/api/gas-guard/weight-scale/devices/'
SITES = '/api/gas-guard/weight-scale/sites/'
FLEET_CONS = '/api/gas-guard/weight-scale/sites/consumption/'


@pytest.fixture
def staff_user(db):
    u = GasGuardUser(email='staff@example.com', is_staff=True)
    u.set_password('x')
    u.save()
    return u


@pytest.fixture
def device(gg_user):
    return ScaleDevice.objects.create(
        device_id='dev-001', name='Kitchen', owner=gg_user,
        tare_kg=Decimal('15.00'), full_gas_kg=Decimal('48.00'),
    )


# ── ingest (device API-key auth) ─────────────────────────────────────────────

@pytest.mark.django_db
def test_ingest_requires_api_key(api_client, device):
    resp = api_client.post(INGEST, {'weight': '40.5'}, format='json')
    assert resp.status_code == 401


@pytest.mark.django_db
def test_ingest_rejects_bad_api_key(api_client, device):
    api_client.credentials(HTTP_X_API_KEY='not-a-real-key')
    resp = api_client.post(INGEST, {'weight': '40.5'}, format='json')
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_ingest_stores_reading_for_authed_device(api_client, device):
    api_client.credentials(HTTP_X_API_KEY=device.api_key)
    resp = api_client.post(
        INGEST, {'weight': '40.500', 'unit': 'kg', 'rssi_dbm': -60}, format='json'
    )
    assert resp.status_code == 201
    # Device comes from the key, not the body.
    assert device.readings.count() == 1
    assert device.readings.first().weight == Decimal('40.500')


@pytest.mark.django_db
def test_ingest_rejects_inactive_device(api_client, device):
    device.is_active = False
    device.save(update_fields=['is_active'])
    api_client.credentials(HTTP_X_API_KEY=device.api_key)
    resp = api_client.post(INGEST, {'weight': '40.5'}, format='json')
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_ingest_missing_weight_is_400(api_client, device):
    api_client.credentials(HTTP_X_API_KEY=device.api_key)
    resp = api_client.post(INGEST, {'unit': 'kg'}, format='json')
    assert resp.status_code == 400


# ── staff-only lists ─────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_device_list_forbidden_for_non_staff(gg_client):
    assert gg_client.get(DEVICE_LIST).status_code == 403


@pytest.mark.django_db
def test_device_list_ok_for_staff(staff_user, device):
    resp = auth_client_for(staff_user).get(DEVICE_LIST)
    assert resp.status_code == 200
    assert len(resp.data) == 1
    # api_key must never be exposed.
    assert 'api_key' not in resp.data[0]


@pytest.mark.django_db
def test_reading_list_forbidden_for_non_staff(gg_client):
    assert gg_client.get(READING_LIST).status_code == 403


# ── owner-scoped sites ───────────────────────────────────────────────────────

@pytest.mark.django_db
def test_sites_requires_auth(api_client):
    assert api_client.get(SITES).status_code == 401


@pytest.mark.django_db
def test_sites_lists_only_owned(gg_user, device):
    # A second user's device must not appear for gg_user.
    other = GasGuardUser(email='other@example.com')
    other.set_password('x')
    other.save()
    ScaleDevice.objects.create(device_id='dev-002', name='Other', owner=other)

    resp = auth_client_for(gg_user).get(SITES)
    assert resp.status_code == 200
    ids = [s['name'] for s in resp.data]
    assert 'Kitchen' in ids and 'Other' not in ids


# ── subscriber gate on consumption ───────────────────────────────────────────

@pytest.mark.django_db
def test_fleet_consumption_forbidden_for_device_tier(gg_client, device):
    assert gg_client.get(FLEET_CONS).status_code == 403


@pytest.mark.django_db
def test_fleet_consumption_ok_for_subscriber(db):
    sub = GasGuardUser(email='s@example.com', tier=GasGuardUser.Tier.SUBSCRIBED)
    sub.set_password('x')
    sub.save()
    ScaleDevice.objects.create(device_id='dev-s', name='S', owner=sub)
    resp = auth_client_for(sub).get(FLEET_CONS)
    assert resp.status_code == 200


# ── services (pure domain logic) ─────────────────────────────────────────────

def test_to_kg_conversions():
    assert to_kg(1000, 'g') == pytest.approx(1.0)
    assert to_kg(2, 'lb') == pytest.approx(0.907, abs=0.01)
    assert to_kg(5, 'kg') == pytest.approx(5.0)


@pytest.mark.django_db
def test_site_payload_no_readings_uses_tare(device):
    payload = site_payload(device, include_estimate=True)
    assert payload['name'] == 'Kitchen'
    assert payload['tareKg'] == float(device.tare_kg)  # 15.0
    assert payload['latest']['totalKg'] == float(device.tare_kg)
    assert payload['connectivity']['online'] is False
    assert payload['estDaysLeft'] is None  # no reading → no estimate


@pytest.mark.django_db
def test_site_payload_online_with_recent_reading(device):
    WeightReading.objects.create(
        device=device, weight=Decimal('40.000'), unit='kg',
        received_at=timezone.now(),
    )
    payload = site_payload(device, include_estimate=False)
    assert payload['connectivity']['online'] is True
    assert payload['latest']['totalKg'] == 40.0


@pytest.mark.django_db
def test_daily_consumption_returns_window_length(device):
    now = timezone.now()
    # Two readings a day apart, gas dropping 40 → 38.
    WeightReading.objects.create(device=device, weight=Decimal('40.0'), unit='kg',
                                 received_at=now - timedelta(days=1))
    WeightReading.objects.create(device=device, weight=Decimal('38.0'), unit='kg',
                                 received_at=now)
    series = daily_consumption(device, days=7, now=now)
    assert len(series) == 7
    assert all('date' in d and 'kg' in d for d in series)
    assert sum(d['kg'] for d in series) >= 0
