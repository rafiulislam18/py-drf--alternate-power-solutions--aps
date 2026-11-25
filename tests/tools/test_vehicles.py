"""
Tool bookings: vehicles (the list in Tool settings, a booking's vehicle, the
Bookings filter and where each tool is on the Tools list).
"""

import pytest

from apps.records.models import AuditLog
from apps.tools.models import Vehicle

from ..client_portal.conftest import api_for
from .conftest import BOOKINGS, TOOLS, add_tool, book, days_ago, give_back, make_staff, vehicle_id

pytestmark = pytest.mark.django_db

VEHICLES = f'{TOOLS}vehicles/'


def test_the_vehicles_are_seeded_in_order(api):
    vehicles = api.get(f'{TOOLS}options/').data['vehicles']
    assert [(v['name'], v['registration']) for v in vehicles] == [
        ('Lorenzo NP200', 'CAA 636 441'), ('Gurshwain NP200', 'CAA 481 962'), ('Brian Isuzu', 'CAA 315 480'),
        ('Lucky Isuzu', 'CAA 650 272'), ('APS Suzuki EECO', 'CAA 760 335'), ('Leonard NP300', 'CAA 61880')]
    assert all(v['is_active'] for v in vehicles)


def test_add_rename_reorder_and_switch_off(api):
    res = api.post(VEHICLES, {'name': '  Sediek   Hilux ', 'registration': ' caa  123 456 '}, format='json')
    assert res.status_code == 201, res.data
    assert (res.data['name'], res.data['registration'], res.data['order']) == ('Sediek Hilux', 'CAA 123 456', 7)
    url = f"{VEHICLES}{res.data['id']}/"
    assert api.patch(url, {'name': 'Sediek Hilux 2.4', 'order': 0}, format='json').status_code == 200
    assert api.patch(url, {'is_active': False}, format='json').data['is_active'] is False
    assert api.get(f'{TOOLS}options/').data['vehicles'][0]['name'] == 'Sediek Hilux 2.4'
    assert AuditLog.objects.filter(object_type='tools.vehicle', object_id=str(res.data['id'])).count() == 3
    # Never deleted.
    assert api.delete(url).status_code in (403, 405) and Vehicle.objects.filter(pk=res.data['id']).exists()


def test_names_and_registrations_are_unique(api):
    res = api.post(VEHICLES, {'name': 'lucky isuzu', 'registration': 'CAA 1'}, format='json')
    assert res.status_code == 400 and 'name' in res.data
    res = api.post(VEHICLES, {'name': 'Spare bakkie', 'registration': 'caa 650 272'}, format='json')
    assert res.status_code == 400 and 'registration' in res.data
    assert api.post(VEHICLES, {'name': ' ', 'registration': ''}, format='json').status_code == 400
    # No registration is fine (and isn't a clash).
    assert api.post(VEHICLES, {'name': 'Trailer'}, format='json').status_code == 201
    assert api.post(VEHICLES, {'name': 'Trailer 2', 'registration': ''}, format='json').status_code == 201


@pytest.mark.parametrize('method,url', [('post', VEHICLES), ('patch', '{vehicle}')])
def test_changing_vehicles_needs_tools_settings(method, url):
    url = url.format(vehicle=f'{VEHICLES}{vehicle_id()}/')
    booker = api_for(make_staff('ayesha', perms=['tools.view', 'tools.book']))
    assert getattr(booker, method)(url, {'name': 'X'}, format='json').status_code == 403


def test_a_booking_can_name_a_vehicle_and_change_it_later(api, lucky):
    drill = add_tool(api)
    res = book(api, lucky, [drill['id']], vehicle=vehicle_id())
    assert res['vehicle'] == {'id': vehicle_id(), 'name': 'Lucky Isuzu', 'registration': 'CAA 650 272'}
    assert AuditLog.objects.get(action='booked_out').summary.endswith('· in Lucky Isuzu · CAA 650 272 · checked straight away')
    url = f"{BOOKINGS}{res['id']}/"
    data = api.patch(url, {'vehicle': vehicle_id('Brian Isuzu')}, format='json').data
    assert data['vehicle']['name'] == 'Brian Isuzu'
    entry = AuditLog.objects.filter(object_type='tools.booking', action='updated').latest('created_at')
    assert (entry.changes['vehicle']['old'], entry.changes['vehicle']['new']) == (
        'Lucky Isuzu · CAA 650 272', 'Brian Isuzu · CAA 315 480')
    assert api.patch(url, {'vehicle': None}, format='json').data['vehicle'] is None
    # Optional.
    assert book(api, lucky, [add_tool(api)['id']])['vehicle'] is None


def test_a_switched_off_vehicle_stays_but_cannot_be_picked(api, lucky):
    leo = make_staff('leonard', perms=[])
    on_it = book(api, lucky, [add_tool(api)['id']], vehicle=vehicle_id())
    Vehicle.objects.filter(pk=vehicle_id()).update(is_active=False)
    res = book(api, leo, [add_tool(api)['id']], vehicle=vehicle_id(), expect=400)
    assert 'vehicle' in res
    url = f"{BOOKINGS}{on_it['id']}/"
    # Its booking keeps it through other edits, but can't get it back once changed.
    assert api.patch(url, {'notes': 'Paarl', 'vehicle': vehicle_id()}, format='json').status_code == 200
    other = book(api, leo, [add_tool(api)['id']])
    res = api.patch(f"{BOOKINGS}{other['id']}/", {'vehicle': vehicle_id()}, format='json')
    assert res.status_code == 400 and 'vehicle' in res.data
    assert api.post(BOOKINGS, {'technician': leo.pk, 'items': [{'tool': add_tool(api)['id']}],
                               'date_out': days_ago(0), 'vehicle': 9999, 'notify': False},
                    format='json').status_code == 400


def test_the_default_vehicle_is_the_one_on_the_persons_last_booking(api, lucky):
    leo = make_staff('leonard', perms=[], first_name='Leonard')
    make_staff('brian', perms=[], first_name='Brian')
    book(api, lucky, [add_tool(api)['id']], date_out=days_ago(10), vehicle=vehicle_id('Brian Isuzu'))
    book(api, lucky, [add_tool(api)['id']], date_out=days_ago(2), vehicle=vehicle_id())
    book(api, lucky, [add_tool(api)['id']], date_out=days_ago(5), vehicle=vehicle_id('Lorenzo NP200'))
    book(api, leo, [add_tool(api)['id']], vehicle=vehicle_id('Leonard NP300'))
    book(api, leo, [add_tool(api)['id']])  # latest has none: no default
    staff = {s['name']: s['last_vehicle'] for s in api.get(f'{TOOLS}options/').data['staff']}
    assert staff['Lucky'] == vehicle_id()
    assert staff['Leonard'] is None
    assert staff['Brian'] is None


def test_bookings_filter_and_search_by_vehicle(api, lucky):
    in_isuzu = book(api, lucky, [add_tool(api)['id']], vehicle=vehicle_id())
    in_np = book(api, lucky, [add_tool(api)['id']], vehicle=vehicle_id('Lorenzo NP200'))
    none = book(api, lucky, [add_tool(api)['id']])
    ids = lambda **p: {r['id'] for r in api.get(BOOKINGS, p).data['results']}  # noqa: E731
    assert ids(vehicle=vehicle_id()) == {in_isuzu['id']}
    assert ids(vehicle='none') == {none['id']}
    assert ids(q='lorenzo') == {in_np['id']}
    assert ids(q='650') == {in_isuzu['id']}


def test_tool_holders_say_which_vehicle(api, lucky):
    leo = make_staff('leonard', perms=[], first_name='Leonard')
    ladder = add_tool(api, name='Ladder', quantity=4)
    drill = add_tool(api)
    mine = book(api, lucky, [(ladder['id'], 2)], vehicle=vehicle_id())
    book(api, leo, [(ladder['id'], 1), drill['id']])
    rows = {r['id']: r for r in api.get(TOOLS).data['results']}
    holders = rows[ladder['id']]['holders']
    assert [(h['quantity'], h['vehicle'] and h['vehicle']['name']) for h in holders] == [(2, 'Lucky Isuzu'), (1, None)]
    assert rows[ladder['id']]['free'] == 1
    assert [r['id'] for r in api.get(TOOLS, {'q': 'isuzu'}).data['results']] == [ladder['id']]
    # A returned booking no longer puts the tool in the vehicle.
    give_back(api, mine)
    rows = {r['id']: r for r in api.get(TOOLS).data['results']}
    assert [h['vehicle'] for h in rows[ladder['id']]['holders']] == [None]


def test_the_trash_preview_and_email_show_the_vehicle(api, lucky, settings, mailoutbox):
    from apps.tools.emails import send_booked
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    b = book(api, lucky, [add_tool(api)['id']], vehicle=vehicle_id())
    assert send_booked(b['id'])
    assert 'Lucky Isuzu · CAA 650 272' in mailoutbox[-1].body
    api.delete(f"{BOOKINGS}{b['id']}/")
    preview = api.get(f"/records/trash/tools.booking/{b['id']}/").data
    assert preview['detail']['vehicle'] == 'Lucky Isuzu · CAA 650 272'
