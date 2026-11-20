"""
Tests for the overdue tools push (apps.push.tools.check_overdue_tools, hourly
07:00–18:00): once per booking when it becomes overdue (the Overdue tile's rule,
long-term kits never), again only after it stopped being overdue and went
overdue again; to the technician, tools.check staff and "Receive admin alerts"
accounts that can see Tool bookings. ``webpush`` is mocked.
"""

import importlib
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.push.models import NotificationPrefs
from apps.push.tasks import push_overdue_tools
from apps.push.tools import check_overdue_tools
from apps.tools.models import Booking, ToolSettings
from tests.client_portal.conftest import api_for

from .conftest import aps_device, endpoint, make_staff, payloads

pytestmark = pytest.mark.django_db
TOOLS = '/tools/'
BOOKINGS = '/tools/bookings/'


@pytest.fixture(autouse=True)
def tool_types(db):
    from django.apps import apps
    importlib.import_module('apps.tools.migrations.0002_seed_types').seed(apps, None)


@pytest.fixture
def boss(admin_user):
    return admin_user


@pytest.fixture
def people(boss):
    p = {
        'tech': make_staff('lucky', ['tools.view'], first_name='Lucky'),
        'checker': make_staff('checker', ['tools.view', 'tools.check']),
        'viewer': make_staff('viewer', ['tools.view']),
        'admin_alerts': make_staff('office', ['tools.view']),
        'alerts_no_tools': make_staff('office2', ['jobs.view']),
        'boss': boss,  # superuser: tools.check (every permission), so it gets them
    }
    for key in ('admin_alerts', 'alerts_no_tools'):
        NotificationPrefs.objects.create(user=p[key], admin_alerts=True)
    for n, key in enumerate(p):
        aps_device(p[key], n)
    return p


def device(people, key):
    return endpoint(list(people).index(key))


def add_tool(api, name):
    from apps.tools.models import ToolType
    res = api.post(TOOLS, {'name': name, 'type': ToolType.objects.get(name='Drills').pk}, format='json')
    assert res.status_code == 201, res.data
    return res.data['id']


def book(api, technician, names, **data):
    tools = [add_tool(api, n) for n in names]
    payload = {'technician': technician.pk, 'items': [{'tool': t, 'quantity': 1} for t in tools],
               'date_out': str(timezone.localdate()), 'notify': False, **data}
    res = api.post(BOOKINGS, payload, format='json')
    assert res.status_code == 201, res.data
    return Booking.objects.get(pk=res.data['id'])


def overdue(booking, days=1):
    Booking.objects.filter(pk=booking.pk).update(due_date=timezone.localdate() - timedelta(days=days),
                                                 date_out=timezone.localdate() - timedelta(days=days + 3))


def test_once_when_it_becomes_overdue(sent, boss, people):
    b = book(api_for(boss), people['tech'], ['Hammer drill', 'Ladder', 'Grinder'])
    assert check_overdue_tools()['new'] == 0  # not overdue yet
    overdue(b)
    assert check_overdue_tools()['new'] == 1
    got = {u: p for u, p in payloads(sent)}
    assert set(got) == {device(people, k) for k in ('tech', 'checker', 'admin_alerts', 'boss')}
    assert got[device(people, 'tech')] == {
        'app': 'aps',
        'title': 'Hammer drill, Ladder +1 more overdue · Lucky',
        'body': f'{b.booking_id} · due {Booking.objects.get(pk=b.pk).due_date:%a %d %b}',
        'tag': f'aps-booking-{b.pk}',
        'url': f'/dashboard/tools/bookings/{b.pk}',
    }
    sent.reset_mock()
    assert check_overdue_tools()['new'] == 0  # next hour / next day: not again
    sent.assert_not_called()


def test_no_due_date_uses_the_overdue_colour(sent, boss, people):
    b = book(api_for(boss), people['tech'], ['Drill'])
    Booking.objects.filter(pk=b.pk).update(due_date=None,
                                           date_out=timezone.localdate() - timedelta(days=ToolSettings.load().overdue_days))
    check_overdue_tools()
    payload = payloads(sent)[0][1]
    assert payload['title'] == 'Drill overdue · Lucky'
    assert payload['body'].startswith(f'{b.booking_id} · out since ')


def test_long_term_kits_never(sent, boss, people):
    b = book(api_for(boss), people['tech'], ['Kit drill'])
    Booking.objects.filter(pk=b.pk).update(long_term_kit=True, due_date=None,
                                           date_out=timezone.localdate() - timedelta(days=400))
    assert check_overdue_tools()['new'] == 0
    sent.assert_not_called()


def test_again_only_after_it_stopped_being_overdue(sent, boss, people):
    b = book(api_for(boss), people['tech'], ['Drill'])
    overdue(b)
    check_overdue_tools()
    # Due date moved to next week: no longer overdue, the stamp clears.
    Booking.objects.filter(pk=b.pk).update(due_date=timezone.localdate() + timedelta(days=7))
    assert check_overdue_tools()['cleared'] == 1
    assert Booking.objects.get(pk=b.pk).overdue_push_at is None
    sent.reset_mock()
    overdue(b)
    assert check_overdue_tools()['new'] == 1
    assert sent.call_count == 4


def test_returned_booking_clears_and_is_never_announced(sent, boss, people):
    api = api_for(boss)
    b = book(api, people['tech'], ['Drill'])
    overdue(b)
    check_overdue_tools()
    item = b.items.get()
    res = api.post(f'{BOOKINGS}{b.pk}/return/', {'items': [{'item': item.pk}], 'date': str(timezone.localdate()),
                                                  'notify': False}, format='json')
    assert res.status_code == 200, res.data
    assert check_overdue_tools()['cleared'] == 1


def test_technician_needs_tools_view_and_switches_apply(sent, boss, people):
    no_view = make_staff('newbie', [], first_name='Newbie')
    aps_device(no_view, 50)
    NotificationPrefs.objects.create(user=people['checker'], off_types=['tool_overdue'])
    b = book(api_for(boss), no_view, ['Drill'])
    overdue(b)
    check_overdue_tools()
    urls = {u for u, _ in payloads(sent)}
    assert endpoint(50) not in urls  # can't open Tool bookings
    assert device(people, 'checker') not in urls  # switched off
    assert device(people, 'admin_alerts') in urls


def test_admin_alerts_off_by_default_even_for_superusers(sent, people):
    plain_boss = make_staff('boss2', ['tools.view'])  # not a checker, no admin alerts
    aps_device(plain_boss, 60)
    b = book(api_for(people['boss']), people['tech'], ['Drill'])
    overdue(b)
    check_overdue_tools()
    assert endpoint(60) not in {u for u, _ in payloads(sent)}


def test_trashed_booking_ignored(sent, boss, people):
    b = book(api_for(boss), people['tech'], ['Drill'])
    overdue(b)
    Booking.objects.get(pk=b.pk).trash(boss)
    assert check_overdue_tools()['new'] == 0


def test_task_runs_the_check(sent, boss, people):
    b = book(api_for(boss), people['tech'], ['Drill'])
    overdue(b)
    assert push_overdue_tools.apply().get()['new'] == 1
