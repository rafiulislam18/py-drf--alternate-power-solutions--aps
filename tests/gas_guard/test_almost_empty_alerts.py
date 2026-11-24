"""
Tests for the second, "Gas almost empty" low-gas level (default 10%, on top of
the 30% one): each level fires once per drop, a drop straight past both sends
only the 10% alert, a refill above 30% resets both (one refill email), a part
refill back above 10% re-arms that level only, and the recipients are the same
as the 30% alert. The email delivery is patched except in the content tests.

Cylinder maths: tare 15 kg, full 48 kg of gas → pct = (total − 15) / 48.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.gas_guard.alerts.emails import left_text, send_low_gas_email
from apps.gas_guard.alerts.models import LowGasAlertState
from apps.gas_guard.alerts.tasks import check_low_gas
from apps.gas_guard.subscription.models import Subscription
from apps.gas_guard.users.models import GasGuardUser
from apps.gas_guard.weight_scale.models import ScaleDevice, WeightReading

pytestmark = pytest.mark.django_db

FINE = '45.0'      # 62%
LOW = '25.0'       # 21%
LOW_29 = '29.0'    # 29%
LOW_15 = '22.0'    # 15%: low, back above almost empty
EMPTY = '19.0'     # 8%
EMPTY_10 = '20.0'  # exactly 10%
REFILL = '46.0'    # 65%


@pytest.fixture(autouse=True)
def levels(settings):
    settings.GAS_GUARD_LOW_GAS_THRESHOLD_PCT = 30
    settings.GAS_GUARD_CRITICAL_GAS_THRESHOLD_PCT = 10
    settings.EMAIL_RECIPIENT = 'info@aps.example'


@pytest.fixture
def emails():
    with patch('apps.gas_guard.alerts.tasks.send_low_gas_email', return_value=True) as mock, \
            patch('apps.gas_guard.alerts.tasks.queue_low_gas_push'):
        yield mock


@pytest.fixture
def owner():
    u = GasGuardUser(email='owner@example.com', tier=GasGuardUser.Tier.SUBSCRIBED)
    u.set_password('x')
    u.save()
    return u


def new_site(owner, total=FINE, monitored=True):
    device = ScaleDevice.objects.create(device_id='dev-1', name='Kitchen', owner=owner,
                                        tare_kg=Decimal('15.00'), full_gas_kg=Decimal('48.00'))
    read(device, total)
    if monitored is not None:
        Subscription.objects.create(user=owner, device=device, is_active=monitored)
    return device


def read(device, total_kg):
    WeightReading.objects.create(device=device, weight=Decimal(total_kg), unit='kg', received_at=timezone.now())


def check():
    check_low_gas.apply().get()


def kinds(mock):
    """What each email was: 'low', 'almost empty' or 'refilled'."""
    out = []
    for c in mock.call_args_list:
        ctx = c.args[0]
        out.append('refilled' if not ctx['is_fault'] else 'almost empty' if ctx.get('is_critical') else 'low')
    return out


def latches(device):
    state = LowGasAlertState.objects.get(device=device)
    return state.is_active, state.is_critical


# ── each level once per drop ─────────────────────────────────────────────────

def test_each_level_fires_once(emails, owner):
    site = new_site(owner)
    check()
    assert kinds(emails) == []

    read(site, LOW)
    check()
    check()  # still low: quiet
    assert kinds(emails) == ['low']
    assert latches(site) == (True, False)

    read(site, EMPTY)
    check()
    check()  # still almost empty: quiet
    assert kinds(emails) == ['low', 'almost empty']
    assert latches(site) == (True, True)


def test_almost_empty_is_at_or_below_the_level(emails, owner):
    site = new_site(owner, LOW)
    check()
    read(site, EMPTY_10)
    check()
    assert kinds(emails) == ['low', 'almost empty']
    assert emails.call_args.args[0]['pct'] == 10


def test_jump_past_both_levels_sends_only_almost_empty(emails, owner):
    site = new_site(owner)
    check()
    read(site, EMPTY)  # 62% → 8% between two checks
    check()
    check()
    assert kinds(emails) == ['almost empty']
    assert latches(site) == (True, True)
    ctx = emails.call_args.args[0]
    assert (ctx['pct'], ctx['threshold']) == (8, 10)


def test_low_alert_still_says_30(emails, owner):
    new_site(owner, LOW_29)
    check()
    ctx = emails.call_args.args[0]
    assert (ctx['pct'], ctx['threshold'], ctx.get('is_critical')) == (29, 30, None)


# ── resets ───────────────────────────────────────────────────────────────────

def test_refill_above_30_resets_both_with_one_email(emails, owner):
    site = new_site(owner, EMPTY)
    check()
    read(site, REFILL)
    check()
    check()
    assert kinds(emails) == ['almost empty', 'refilled']
    assert latches(site) == (False, False)
    assert emails.call_args.args[0]['threshold'] == 30  # "above the 30% threshold"

    # Both levels fire again on the next drop.
    read(site, LOW)
    check()
    read(site, EMPTY)
    check()
    assert kinds(emails) == ['almost empty', 'refilled', 'low', 'almost empty']


def test_part_refill_above_10_resets_the_10_level_only(emails, owner):
    site = new_site(owner, EMPTY)
    check()

    read(site, LOW_15)  # back above 10% but still low
    check()
    assert kinds(emails) == ['almost empty']  # no refill email, no new low email
    assert latches(site) == (True, False)

    read(site, LOW)  # still low: the 30% level stays latched
    check()
    assert kinds(emails) == ['almost empty']

    read(site, EMPTY)  # almost empty again
    check()
    assert kinds(emails) == ['almost empty', 'almost empty']

    read(site, REFILL)
    check()
    assert kinds(emails) == ['almost empty', 'almost empty', 'refilled']
    assert latches(site) == (False, False)


# ── recipients: the same as the 30% alert ────────────────────────────────────

def recipients(mock):
    return [c.args[1] for c in mock.call_args_list]


def test_monitored_site_emails_owner_and_office(emails, owner):
    owner.notify_email = 'alerts@owner.example'
    owner.save()
    site = new_site(owner, LOW)
    check()
    read(site, EMPTY)
    check()
    assert recipients(emails) == [['alerts@owner.example', 'info@aps.example']] * 2


@pytest.mark.parametrize('monitored', [None, False])  # never subscribed / cancelled
def test_unmonitored_site_emails_the_office_only(emails, owner, monitored):
    new_site(owner, EMPTY, monitored=monitored)
    check()
    assert kinds(emails) == ['almost empty']
    assert recipients(emails) == [['info@aps.example']]


def test_switch_off_means_no_owner_email(emails, owner):
    owner.email_alerts_enabled = False
    owner.save()
    new_site(owner, EMPTY)
    check()
    assert recipients(emails) == [['info@aps.example']]


# ── the email itself ─────────────────────────────────────────────────────────

def test_left_text():
    assert left_text(8, 3) == '8% left (~3 days)'
    assert left_text(8, 1) == '8% left (~1 day)'
    assert left_text(8, None) == '8% left'  # no usage history to estimate from


def _context(**kw):
    return {'site_name': 'Kitchen', 'pct': 8, 'gas_kg': 4.0, 'threshold': 10, 'critical_threshold': 10,
            'est_days': 3, 'checked_at': '24/11/2026 10:00:00', 'is_fault': True, 'is_critical': True, **kw}


def test_almost_empty_email_wording(settings, mailoutbox):
    settings.ALERT_EMAIL_ENABLED = True
    assert send_low_gas_email(_context(), ['info@aps.example']) is True
    mail = mailoutbox[0]
    assert mail.subject == 'Gas almost empty: Kitchen'
    assert '8% left (~3 days)' in mail.body
    assert 'ALMOST EMPTY' in mail.body
    assert 'at or below the 10% level' in mail.body


def test_low_email_wording_is_unchanged(settings, mailoutbox):
    settings.ALERT_EMAIL_ENABLED = True
    send_low_gas_email(_context(pct=21, threshold=30, is_critical=False), ['info@aps.example'])
    assert mailoutbox[0].subject == 'Gas Guard: Kitchen is low on gas'
    assert 'at or below the 30%' in mailoutbox[0].body


def test_site_name_is_escaped_in_the_email(settings, mailoutbox):
    settings.ALERT_EMAIL_ENABLED = True
    send_low_gas_email(_context(site_name='<b>Kitchen</b>'), ['info@aps.example'])
    assert '<b>Kitchen</b>' not in mailoutbox[0].body


def test_str_shows_almost_empty(owner):
    state = LowGasAlertState.objects.create(device=new_site(owner), is_active=True, is_critical=True)
    assert 'ALMOST EMPTY' in str(state)
