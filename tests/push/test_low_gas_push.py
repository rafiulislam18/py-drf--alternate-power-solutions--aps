"""
Tests for the Gas Guard low-gas push (apps.push.gas), sent from the hourly
check at the same moment as the email: once on the drop, never while it stays
low or on a refill, again after a refill and a new drop. Goes to Gas Guard
admins for every site, and to the owner only while that site has an active
Monitoring subscription (a paid feature, per site). The 10% "Gas almost empty"
push follows the same rules, with the same tag so it replaces the 30% one.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.gas_guard.alerts.tasks import check_low_gas
from apps.gas_guard.subscription.models import Subscription
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
    return make_gg_user('owner@example.com', tier=GasGuardUser.Tier.SUBSCRIBED)


def new_site(owner, device_id='dev-1', name='Kitchen', monitored=True):
    device = ScaleDevice.objects.create(device_id=device_id, name=name, owner=owner,
                                        tare_kg=Decimal('15.00'), full_gas_kg=Decimal('48.00'))
    read(device, '45.0')  # ~91%: fine
    if monitored is not None:
        Subscription.objects.create(user=owner, device=device, is_active=monitored)
    return device


@pytest.fixture
def site(owner):
    return new_site(owner)  # on the Monitoring plan


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
    read(site, '25.0')  # 10 kg of 48 → 21%
    run_check(django_capture_on_commit_callbacks)
    assert sorted(url for url, _ in payloads(sent)) == [endpoint(1), endpoint(2)]
    assert payloads(sent)[0][1] == {
        'app': 'gas_guard',
        'title': 'Low gas: Kitchen',
        'body': 'Kitchen is down to 21% gas. Arrange a refill before it runs out.',
        'tag': f'gg-site-{site.pk}',
        'url': f'/gas-guard/dashboard/sites/{site.pk}',
    }


def test_once_per_drop_not_every_hour_not_on_refill(sent, no_email, site, recipients,
                                                    django_capture_on_commit_callbacks):
    run_check(django_capture_on_commit_callbacks)  # fine: nothing
    assert sent.call_count == 0

    read(site, '25.0')
    run_check(django_capture_on_commit_callbacks)  # drop: push
    run_check(django_capture_on_commit_callbacks)  # still low: quiet
    assert sent.call_count == 2  # owner + admin, once

    read(site, '46.0')
    run_check(django_capture_on_commit_callbacks)  # refill: email only
    assert sent.call_count == 2
    assert no_email.call_count == 2  # the low email + the refill email

    read(site, '24.0')
    run_check(django_capture_on_commit_callbacks)  # dropped again: push again
    assert sent.call_count == 4


def test_owner_who_is_also_admin_gets_one(sent, owner, site, django_capture_on_commit_callbacks):
    owner.is_staff = True
    owner.save()
    gg_device(owner, 1)
    read(site, '25.0')
    run_check(django_capture_on_commit_callbacks)
    assert sent.call_count == 1


def test_site_without_owner_still_reaches_admins(sent, site, recipients, django_capture_on_commit_callbacks):
    site.owner = None
    site.save()
    read(site, '25.0')
    run_check(django_capture_on_commit_callbacks)
    assert [url for url, _ in payloads(sent)] == [endpoint(2)]


def test_push_failure_doesnt_stop_the_check(site, recipients, vapid, django_capture_on_commit_callbacks):
    read(site, '25.0')
    with patch('apps.push.send.webpush', side_effect=RuntimeError('boom')):
        run_check(django_capture_on_commit_callbacks)
    assert site.low_gas_alert_state.is_active is True


# ── Low-gas alerts are part of the Monitoring plan (per site) ─────────────


def owner_urls(mock):
    return [u for u, _ in payloads(mock)]


def test_site_without_subscription_alerts_admins_only(sent, owner, recipients, django_capture_on_commit_callbacks):
    device = new_site(owner, monitored=None)  # never subscribed
    read(device, '25.0')
    run_check(django_capture_on_commit_callbacks)
    assert owner_urls(sent) == [endpoint(2)]  # the Gas Guard admin, not the owner


def test_cancelled_subscription_alerts_admins_only(sent, owner, recipients, django_capture_on_commit_callbacks):
    device = new_site(owner, monitored=False)  # cancelled (PayFast CANCELLED or in Plans)
    read(device, '25.0')
    run_check(django_capture_on_commit_callbacks)
    assert owner_urls(sent) == [endpoint(2)]


def test_checked_per_site_not_per_account(sent, owner, recipients, django_capture_on_commit_callbacks):
    # The owner pays for one site (account tier "subscribed"), not this one.
    new_site(owner, device_id='dev-paid', name='Paid', monitored=True)
    unpaid = new_site(owner, device_id='dev-unpaid', name='Unpaid', monitored=None)
    read(unpaid, '25.0')
    run_check(django_capture_on_commit_callbacks)
    assert owner_urls(sent) == [endpoint(2)]
    assert payloads(sent)[0][1]['title'] == 'Low gas: Unpaid'


def test_subscribing_while_low_doesnt_alert_until_the_next_drop(sent, owner, recipients,
                                                               django_capture_on_commit_callbacks):
    device = new_site(owner, monitored=None)
    read(device, '25.0')
    run_check(django_capture_on_commit_callbacks)  # admins told; owner not subscribed
    Subscription.objects.create(user=owner, device=device, is_active=True)  # subscribes now
    sent.reset_mock()
    run_check(django_capture_on_commit_callbacks)  # still low: quiet
    sent.assert_not_called()
    read(device, '46.0')
    run_check(django_capture_on_commit_callbacks)  # refill
    read(device, '24.0')
    run_check(django_capture_on_commit_callbacks)  # next drop: the owner too
    assert sorted(owner_urls(sent)) == [endpoint(1), endpoint(2)]


# ── The second, "Gas almost empty" level (10%) ───────────────────────────────


def almost_empty(site, pct, days):
    return {
        'app': 'gas_guard',
        'title': f'Gas almost empty: {site.name}',
        'body': f'{pct}% left (~{days} days)',
        'tag': f'gg-site-{site.pk}',  # same as the 30% one: replaces it on the device
        'url': f'/gas-guard/dashboard/sites/{site.pk}',
    }


@pytest.fixture
def three_days():
    with patch('apps.gas_guard.weight_scale.services.estimate_days_left', return_value=3):
        yield


def test_almost_empty_pushes_owner_and_admins(sent, site, recipients, three_days,
                                              django_capture_on_commit_callbacks):
    read(site, '25.0')
    run_check(django_capture_on_commit_callbacks)  # 21%: the 30% push
    sent.reset_mock()
    read(site, '19.0')  # 4 kg of 48 → 8%
    run_check(django_capture_on_commit_callbacks)
    run_check(django_capture_on_commit_callbacks)  # still almost empty: quiet
    assert sorted(url for url, _ in payloads(sent)) == [endpoint(1), endpoint(2)]
    assert payloads(sent)[0][1] == almost_empty(site, 8, 3)
    assert payloads(sent)[0][1]['tag'] == f'gg-site-{site.pk}'


def test_almost_empty_without_an_estimate(sent, site, recipients, django_capture_on_commit_callbacks):
    with patch('apps.gas_guard.weight_scale.services.estimate_days_left', return_value=None):
        read(site, '19.0')
        run_check(django_capture_on_commit_callbacks)
    assert payloads(sent)[0][1]['body'] == '8% left'


def test_jump_past_both_levels_pushes_only_almost_empty(sent, site, recipients, three_days,
                                                       django_capture_on_commit_callbacks):
    read(site, '19.0')  # 91% → 8% between two checks
    run_check(django_capture_on_commit_callbacks)
    assert sent.call_count == 2  # owner + admin, one push each
    assert {p['title'] for _, p in payloads(sent)} == {'Gas almost empty: Kitchen'}


def test_almost_empty_refill_resets(sent, no_email, site, recipients, django_capture_on_commit_callbacks):
    read(site, '19.0')
    run_check(django_capture_on_commit_callbacks)  # almost empty: push
    read(site, '22.0')  # 15%: back above 10%, still low — no push, no email
    run_check(django_capture_on_commit_callbacks)
    assert (sent.call_count, no_email.call_count) == (2, 1)
    read(site, '19.0')  # almost empty again: push again
    run_check(django_capture_on_commit_callbacks)
    assert sent.call_count == 4
    read(site, '46.0')  # refill: email only
    run_check(django_capture_on_commit_callbacks)
    assert (sent.call_count, no_email.call_count) == (4, 3)


def test_almost_empty_owner_who_is_also_admin_gets_one(sent, owner, site, django_capture_on_commit_callbacks):
    owner.is_staff = True
    owner.save()
    gg_device(owner, 1)
    read(site, '19.0')
    run_check(django_capture_on_commit_callbacks)
    assert sent.call_count == 1
    assert payloads(sent)[0][1]['title'] == 'Gas almost empty: Kitchen'


def test_almost_empty_unsubscribed_site_alerts_admins_only(sent, owner, recipients,
                                                          django_capture_on_commit_callbacks):
    device = new_site(owner, monitored=None)
    read(device, '19.0')
    run_check(django_capture_on_commit_callbacks)
    assert owner_urls(sent) == [endpoint(2)]


def test_queued_before_the_change_still_sends(sent, site, recipients):
    # A push queued with the old three arguments (before a deploy) still goes out as the 30% one.
    from apps.push.tasks import push_low_gas
    push_low_gas(site.pk, 'Kitchen', 21)
    assert payloads(sent)[0][1]['title'] == 'Low gas: Kitchen'
