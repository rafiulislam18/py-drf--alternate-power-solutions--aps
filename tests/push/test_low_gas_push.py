"""
Tests for the Gas Guard low-gas push (apps.push.gas), sent from the hourly
check at the same moment as the email: once on the drop, never while it stays
low or on a refill, again after a refill and a new drop. Goes to the owner
(whatever their plan) and Gas Guard admins.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.gas_guard.alerts.tasks import check_low_gas
from apps.gas_guard.users.models import GasGuardUser
from apps.gas_guard.weight_scale.models import ScaleDevice, WeightReading
from tests.client_portal.conftest import make_client

from .conftest import aps_device, endpoint, gg_device, make_gg_user, payloads

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_email(settings):
    settings.GAS_GUARD_LOW_GAS_THRESHOLD_PCT = 30
    with patch('apps.gas_guard.alerts.tasks.send_low_gas_email', return_value=True) as mock:
        yield mock


@pytest.fixture
def owner():
    # Device-only plan: low-gas alerts go to every owner, like the email.
    return make_gg_user('owner@example.com', tier=GasGuardUser.Tier.DEVICE)


@pytest.fixture
def site(owner):
    device = ScaleDevice.objects.create(device_id='dev-1', name='Kitchen', owner=owner,
                                        tare_kg=Decimal('15.00'), full_gas_kg=Decimal('48.00'))
    read(device, '45.0')  # ~91%: fine
    return device


def read(device, total_kg):
    WeightReading.objects.create(device=device, weight=Decimal(total_kg), unit='kg', received_at=timezone.now())


def run_check(capture):
    with capture(execute=True):
        check_low_gas.apply().get()


@pytest.fixture
def recipients(owner):
    admin = make_gg_user('admin@example.com', is_staff=True)
    stranger = make_gg_user('someone@example.com')
    retired = make_gg_user('old-admin@example.com', is_staff=True, is_active=False)
    return {
        'owner': gg_device(owner, 1),
        'admin': gg_device(admin, 2),
        'stranger': gg_device(stranger, 3),
        'retired': gg_device(retired, 4),
        # An APS dashboard device never gets Gas Guard alerts.
        'aps': aps_device(make_client('acme'), 5),
    }


def test_drop_pushes_owner_and_gas_guard_admins(sent, site, recipients, django_capture_on_commit_callbacks):
    read(site, '20.0')  # 5 kg of 48 → 10%
    run_check(django_capture_on_commit_callbacks)
    assert sorted(url for url, _ in payloads(sent)) == [endpoint(1), endpoint(2)]
    assert payloads(sent)[0][1] == {
        'app': 'gas_guard',
        'title': 'Low gas: Kitchen',
        'body': 'Kitchen is down to 10% gas. Arrange a refill before it runs out.',
        'tag': f'gg-site-{site.pk}',
        'url': f'/gas-guard/dashboard/sites/{site.pk}',
    }


def test_once_per_drop_not_every_hour_not_on_refill(sent, no_email, site, recipients,
                                                    django_capture_on_commit_callbacks):
    run_check(django_capture_on_commit_callbacks)  # fine: nothing
    assert sent.call_count == 0

    read(site, '20.0')
    run_check(django_capture_on_commit_callbacks)  # drop: push
    run_check(django_capture_on_commit_callbacks)  # still low: quiet
    assert sent.call_count == 2  # owner + admin, once

    read(site, '46.0')
    run_check(django_capture_on_commit_callbacks)  # refill: email only
    assert sent.call_count == 2
    assert no_email.call_count == 2  # the low email + the refill email

    read(site, '18.0')
    run_check(django_capture_on_commit_callbacks)  # dropped again: push again
    assert sent.call_count == 4


def test_owner_who_is_also_admin_gets_one(sent, owner, site, django_capture_on_commit_callbacks):
    owner.is_staff = True
    owner.save()
    gg_device(owner, 1)
    read(site, '20.0')
    run_check(django_capture_on_commit_callbacks)
    assert sent.call_count == 1


def test_site_without_owner_still_reaches_admins(sent, site, recipients, django_capture_on_commit_callbacks):
    site.owner = None
    site.save()
    read(site, '20.0')
    run_check(django_capture_on_commit_callbacks)
    assert [url for url, _ in payloads(sent)] == [endpoint(2)]


def test_push_failure_doesnt_stop_the_check(site, recipients, vapid, django_capture_on_commit_callbacks):
    read(site, '20.0')
    with patch('apps.push.send.webpush', side_effect=RuntimeError('boom')):
        run_check(django_capture_on_commit_callbacks)
    assert site.low_gas_alert_state.is_active is True
