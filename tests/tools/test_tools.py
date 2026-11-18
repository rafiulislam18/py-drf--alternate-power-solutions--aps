"""
Tool bookings (apps.tools): the register, book-outs and returns, checks,
colours and overdue, archive, trash, audit history and permissions.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.records.models import AuditLog
from apps.tools.models import Booking, BookingItem, Movement, Tool, ToolSettings

from ..client_portal.conftest import api_for
from .conftest import BOOKINGS, TOOLS, add_tool, book, days_ago, give_back, make_staff, type_id

pytestmark = pytest.mark.django_db

BOOKER = ['tools.view', 'tools.book']
CHECKER = ['tools.view', 'tools.book', 'tools.check']


def booking_obj(data):
    return Booking.all_objects.get(pk=data['id'])


# ── the register ─────────────────────────────────────────────────────────────

def test_tools_are_numbered_and_trashed_numbers_stay_taken(api):
    a, b = add_tool(api), add_tool(api, name='Grinder')
    assert (a['tool_id'], b['tool_id']) == ('TL-001', 'TL-002')
    assert api.delete(f"{TOOLS}{b['id']}/").status_code == 204
    assert add_tool(api)['tool_id'] == 'TL-003'
    assert api.get(f'{TOOLS}options/').data['next_tool_id'] == 'TL-004'


def test_a_tool_needs_a_name_and_an_active_type(api):
    res = api.post(TOOLS, {'name': ' ', 'type': type_id()}, format='json')
    assert res.status_code == 400 and 'name' in res.data
    t = type_id('Saws')
    api.patch(f'{TOOLS}types/{t}/', {'is_active': False}, format='json')
    res = api.post(TOOLS, {'name': 'Jigsaw', 'type': t}, format='json')
    assert res.status_code == 400 and 'type' in res.data


def test_availability_counts_units_and_condition(api, lucky):
    ladder = add_tool(api, name='Ladder', type=type_id('Ladders & access'), quantity=4)
    drill = add_tool(api)
    book(api, lucky, [(ladder['id'], 3)])
    rows = {r['id']: r for r in api.get(TOOLS).data['results']}
    assert (rows[ladder['id']]['availability'], rows[ladder['id']]['free']) == ('partly', 1)
    assert rows[ladder['id']]['holders'][0]['technician']['name'] == 'Lucky'
    assert rows[drill['id']]['availability'] == 'available'
    api.patch(f"{TOOLS}{drill['id']}/", {'condition': 'in_repair'}, format='json')
    rows = {r['id']: r for r in api.get(TOOLS).data['results']}
    assert (rows[drill['id']]['availability'], rows[drill['id']]['free']) == ('oos', 0)
    # Filters
    assert [r['id'] for r in api.get(TOOLS, {'available': 1}).data['results']] == [ladder['id']]
    assert [r['id'] for r in api.get(TOOLS, {'q': 'lucky'}).data['results']] == [ladder['id']]


def test_quantity_cannot_drop_below_the_units_out(api, lucky):
    ladder = add_tool(api, name='Ladder', quantity=4)
    book(api, lucky, [(ladder['id'], 3)])
    res = api.patch(f"{TOOLS}{ladder['id']}/", {'quantity': 2}, format='json')
    assert res.status_code == 400 and 'quantity' in res.data
    assert api.patch(f"{TOOLS}{ladder['id']}/", {'quantity': 3}, format='json').status_code == 200


def test_a_tool_out_cannot_go_to_the_trash(api, lucky):
    drill = add_tool(api)
    b = book(api, lucky, [drill['id']])
    assert api.delete(f"{TOOLS}{drill['id']}/").status_code == 400
    give_back(api, b)
    assert api.delete(f"{TOOLS}{drill['id']}/").status_code == 204
    assert Tool.all_objects.get(pk=drill['id']).in_trash


# ── book out ─────────────────────────────────────────────────────────────────

def test_book_out_several_tools_in_one_booking(api, lucky):
    a, b = add_tool(api), add_tool(api, name='Grinder', type=type_id('Grinders'))
    res = book(api, lucky, [a['id'], b['id']], due_date=days_ago(-7), job='', notes='Paarl roof')
    assert res['booking_id'] == 'TB-0001'
    assert [i['tool']['name'] for i in res['items']] == ['Hammer drill', 'Grinder']
    assert (res['status'], res['units'], res['open_units']) == ('out', 2, 2)
    assert res['notes'] == 'Paarl roof'
    entry = AuditLog.objects.get(action='booked_out')
    assert entry.summary.startswith('Booked out to Lucky: Hammer drill, Grinder')


def test_only_in_service_tools_with_free_units_can_be_booked(api, lucky):
    drill = add_tool(api, condition='in_repair')
    res = book(api, lucky, [drill['id']], expect=400)
    assert 'in repair' in str(res['items'])
    ladder = add_tool(api, name='Ladder', quantity=2)
    res = book(api, lucky, [(ladder['id'], 3)], expect=400)
    assert 'Only 2 of 2' in str(res['items'])


def test_dates_are_checked(api, lucky):
    drill = add_tool(api)
    res = book(api, lucky, [drill['id']], date_out=days_ago(-1), expect=400)
    assert 'date_out' in res
    res = book(api, lucky, [drill['id']], date_out=days_ago(3), due_date=days_ago(4), expect=400)
    assert 'due_date' in res
    res = book(api, lucky, [drill['id']], date_out=days_ago(3), due_date=days_ago(5), long_term_kit=True)
    assert res['due_date'] is None  # a kit has no due date


def test_a_job_can_be_linked_by_its_number(api, lucky):
    import importlib
    from django.apps import apps
    importlib.import_module('apps.jobs.migrations.0002_seed_categories').seed(apps, None)
    from apps.jobs.models import JobCategory
    job = api.post('/jobs/', {'client_name': 'Acme', 'category': JobCategory.objects.first().pk}, format='json').data
    drill = add_tool(api)
    res = book(api, lucky, [drill['id']], job='aps-1')
    assert res['job'] == {'id': job['id'], 'job_id': 'APS-001'}
    res = book(api, lucky, [add_tool(api)['id']], job='APS-99', expect=400)
    assert 'job' in res


def test_handover_needs_confirming_and_returns_it_from_the_old_booking(api, lucky):
    leo = make_staff('leonard', perms=[])
    drill = add_tool(api)
    first = book(api, lucky, [drill['id']])
    res = book(api, leo, [drill['id']], expect=409)
    assert res['code'] == 'handover'
    assert res['conflicts'][0]['holder'] == 'Lucky' and res['conflicts'][0]['booking_id'] == 'TB-0001'
    # The same person can't "take it over" from themselves.
    assert book(api, lucky, [drill['id']], confirm_handover=True, expect=400)

    second = book(api, leo, [drill['id']], confirm_handover=True)
    old = booking_obj(first)
    assert not old.has_open_items() and old.is_archived  # every tool back → archived
    item = old.items.get()
    assert item.return_note == f"Handed over to Leonard in {second['booking_id']}"
    assert second['handover_events'][0]['booking_id'] == 'TB-0001'


def test_multi_unit_tools_are_never_handed_over(api, lucky):
    leo = make_staff('leonard', perms=[])
    ladder = add_tool(api, name='Ladder', quantity=1)
    tester = add_tool(api, name='Earth leakage tester', quantity=3)
    book(api, lucky, [(tester['id'], 3), ladder['id']])
    res = book(api, leo, [tester['id']], confirm_handover=True, expect=400)
    assert 'All 3' in str(res['items'])


def test_bookings_in_the_trash_free_their_tools(api, lucky):
    drill = add_tool(api)
    b = book(api, lucky, [drill['id']])
    assert api.delete(f"{BOOKINGS}{b['id']}/").status_code == 204
    assert api.get(f"{TOOLS}{drill['id']}/").data['availability'] == 'available'
    book(api, make_staff('leonard', perms=[]), [drill['id']])
    # Restoring the old booking would double-book the drill.
    assert 'booked out again' in booking_obj(b).restore_blocked_reason()


def test_a_booking_with_a_return_cannot_go_to_the_trash(api, lucky):
    a, b = add_tool(api), add_tool(api, name='Grinder')
    res = book(api, lucky, [a['id'], b['id']])
    give_back(api, res, [res['items'][0]['id']])
    assert api.delete(f"{BOOKINGS}{res['id']}/").status_code == 400


# ── return ───────────────────────────────────────────────────────────────────

def test_partial_return_then_the_rest_archives_the_booking(api, lucky):
    a, b = add_tool(api), add_tool(api, name='Grinder')
    res = book(api, lucky, [a['id'], b['id']])
    after = give_back(api, res, [res['items'][0]['id']])
    assert (after['status'], after['archive_event']) == ('partly', None)
    assert not booking_obj(res).is_archived
    done = give_back(api, after)
    assert done['status'] == 'returned'
    assert done['archive_event'] == {'action': 'archived', 'automatic': True, 'note': 'all tools returned'}
    assert booking_obj(res).archive_reason == 'auto'
    assert api.get(f"{TOOLS}{b['id']}/").data['availability'] == 'available'


def test_returning_some_units_splits_the_item(api, lucky):
    ladder = add_tool(api, name='Ladder', quantity=4)
    res = book(api, lucky, [(ladder['id'], 3)])
    after = give_back(api, res, [{'item': res['items'][0]['id'], 'quantity': 2}])
    assert sorted((i['quantity'], i['out']) for i in after['items']) == [(1, True), (2, False)]
    assert after['status'] == 'partly'
    assert api.get(f"{TOOLS}{ladder['id']}/").data['free'] == 3


def test_damaged_needs_a_note_and_can_send_the_tool_to_repair(api, lucky):
    drill = add_tool(api)
    res = book(api, lucky, [drill['id']])
    line = {'item': res['items'][0]['id'], 'condition': 'damaged'}
    assert 'items' in give_back(api, res, [line], expect=400)
    give_back(api, res, [{**line, 'note': 'Chuck sticking', 'to_repair': True}])
    tool = Tool.objects.get(pk=drill['id'])
    assert tool.condition == 'in_repair'
    item = BookingItem.objects.get(booking_id=res['id'])
    assert (item.return_condition, item.return_note) == ('damaged', 'Chuck sticking')
    assert AuditLog.objects.filter(object_type='tools.tool', action='updated',
                                   summary__startswith='Came back damaged').exists()


def test_return_date_rules(api, lucky):
    drill = add_tool(api)
    res = book(api, lucky, [drill['id']], date_out=days_ago(5))
    assert 'date' in give_back(api, res, date=days_ago(6), expect=400)
    assert 'date' in give_back(api, res, date=days_ago(-1), expect=400)
    give_back(api, res, date=days_ago(2))


# ── checks ───────────────────────────────────────────────────────────────────

def test_movements_wait_for_a_checker_unless_a_checker_entered_them(lucky):
    booker = api_for(make_staff('ayesha', perms=BOOKER + ['tools.create']))
    checker_user = make_staff('sediek', perms=CHECKER)
    checker = api_for(checker_user)
    a, b = add_tool(booker), add_tool(booker, name='Grinder')

    waiting = book(booker, lucky, [a['id']])
    assert waiting['check'] == {'state': 'waiting', 'kind': 'out', 'count': 1}
    mine = book(checker, lucky, [b['id']])
    assert mine['check']['state'] == 'checked' and mine['check']['by']['name'] == 'Sediek'

    assert booker.post(f'{BOOKINGS}check/', {'bookings': [waiting['id']]}, format='json').status_code == 403
    res = checker.post(f'{BOOKINGS}check/', {'bookings': [waiting['id'], mine['id']]}, format='json')
    assert res.data == {'done': [waiting['id']], 'skipped': [mine['id']]}
    assert checker.get(f"{BOOKINGS}{waiting['id']}/").data['check']['state'] == 'checked'
    assert AuditLog.objects.filter(action='checked', summary='Checked the book-out').exists()


def test_a_flagged_problem_needs_a_note_and_stays_on_record(lucky):
    checker = api_for(make_staff('sediek', perms=CHECKER + ['tools.create']))
    booker = api_for(make_staff('ayesha', perms=BOOKER))
    drill = add_tool(checker)
    res = book(booker, lucky, [drill['id']])
    assert checker.post(f'{BOOKINGS}check/', {'bookings': [res['id']], 'problem': True},
                        format='json').status_code == 400
    checker.post(f'{BOOKINGS}check/', {'bookings': [res['id']], 'problem': True, 'note': 'Charger missing'},
                 format='json')
    data = checker.get(f"{BOOKINGS}{res['id']}/").data
    assert data['check']['state'] == 'flagged' and data['check']['note'] == 'Charger missing'
    assert checker.get(BOOKINGS, {'check': 'flagged'}).data['count'] == 1


def test_waiting_returns_stay_visible_after_the_booking_is_archived(lucky):
    booker = api_for(make_staff('ayesha', perms=BOOKER + ['tools.create']))
    drill = add_tool(booker)
    res = book(booker, lucky, [drill['id']])
    give_back(booker, res)
    assert booking_obj(res).is_archived
    assert booker.get(BOOKINGS).data['count'] == 0  # off the active list…
    assert [r['id'] for r in booker.get(BOOKINGS, {'tile': 'check'}).data['results']] == [res['id']]  # …not the queue
    assert [r['id'] for r in booker.get(BOOKINGS, {'check': 'waiting'}).data['results']] == [res['id']]
    assert booker.get(BOOKINGS).data['summary']['waiting'] == 1


# ── colours and overdue ──────────────────────────────────────────────────────

def test_strip_levels_and_overdue(api, lucky):
    s = ToolSettings.load()  # 14 / 30 / 60, overdue from orange (30)
    assert (s.watch_days, s.aging_days, s.critical_days, s.overdue_days) == (14, 30, 60, 30)
    tools = [add_tool(api, name=f'T{n}')['id'] for n in range(6)]
    fresh = book(api, lucky, [tools[0]], date_out=days_ago(3))
    watch = book(api, lucky, [tools[1]], date_out=days_ago(20))
    aging = book(api, lucky, [tools[2]], date_out=days_ago(31))
    kit = book(api, lucky, [tools[3]], date_out=days_ago(90), long_term_kit=True)
    late = book(api, lucky, [tools[4]], date_out=days_ago(3), due_date=days_ago(1))
    due_ok = book(api, lucky, [tools[5]], date_out=days_ago(20), due_date=days_ago(-5))
    got = {r['id']: r for r in api.get(BOOKINGS).data['results']}
    assert [(got[b['id']]['level'], got[b['id']]['overdue']) for b in (fresh, watch, aging, kit, late, due_ok)] == [
        (0, False), (1, False), (2, True), (0, False), (3, True), (1, False)]
    assert got[late['id']]['status'] == 'overdue'
    overdue = {r['id'] for r in api.get(BOOKINGS, {'tile': 'overdue'}).data['results']}
    assert overdue == {aging['id'], late['id']}
    assert api.get(BOOKINGS).data['summary']['overdue'] == 2

    api.patch(f'{TOOLS}settings/', {'overdue_from': 'watch'}, format='json')
    assert api.get(BOOKINGS).data['summary']['overdue'] == 3


def test_due_date_can_change_later_and_is_in_the_history(api, lucky):
    drill = add_tool(api)
    res = book(api, lucky, [drill['id']], date_out=days_ago(3))
    url = f"{BOOKINGS}{res['id']}/"
    assert 'due_date' in api.patch(url, {'due_date': days_ago(4)}, format='json').data
    data = api.patch(url, {'due_date': days_ago(-7)}, format='json').data
    assert data['due_date'] == timezone.localdate() + timedelta(days=7)
    entry = AuditLog.objects.filter(object_type='tools.booking', action='updated').latest('created_at')
    assert entry.changes['due_date']['old'] == '' and entry.changes['due_date']['new']
    # A kit has no due date.
    assert api.patch(url, {'long_term_kit': True}, format='json').data['due_date'] is None
    assert 'due_date' in api.patch(url, {'due_date': days_ago(-3)}, format='json').data


def test_settings_need_increasing_days_and_real_emails(api):
    url = f'{TOOLS}settings/'
    assert 'aging_days' in api.patch(url, {'aging_days': 10}, format='json').data
    assert 'alert_emails' in api.patch(url, {'alert_emails': 'info@aps.example, nope'}, format='json').data
    data = api.patch(url, {'alert_emails': 'info@aps.example; Info@aps.example\naccounts@aps.example'},
                     format='json').data
    assert data['alert_emails'] == 'info@aps.example, accounts@aps.example'


# ── archive ──────────────────────────────────────────────────────────────────

def test_archive_by_hand_only_once_everything_is_back(api, lucky):
    a, b = add_tool(api), add_tool(api, name='Grinder')
    out = book(api, lucky, [a['id']])
    res = book(api, lucky, [b['id']])
    give_back(api, res)
    api.post(f"{BOOKINGS}{res['id']}/unarchive/")
    assert api.post(f"{BOOKINGS}{out['id']}/archive/").status_code == 400
    assert api.post(f"{BOOKINGS}{res['id']}/archive/").status_code == 200
    bulk = api.post(f'{BOOKINGS}bulk-archive/', {'ids': [out['id'], res['id']], 'archived': True}, format='json')
    assert bulk.data == {'done': [], 'skipped': [out['id'], res['id']]}
    bulk = api.post(f'{BOOKINGS}bulk-archive/', {'ids': [res['id']], 'archived': False}, format='json')
    assert bulk.data['done'] == [res['id']]


# ── lists ────────────────────────────────────────────────────────────────────

def test_booking_filters_and_search(api, lucky):
    leo = make_staff('leonard', perms=[], first_name='Leonard')
    a = add_tool(api, name='Wall chaser', serial='WCL15008')
    b = add_tool(api, name='Grinder')
    one = book(api, lucky, [a['id']], date_out=days_ago(40))
    two = book(api, leo, [b['id']])
    ids = lambda **p: [r['id'] for r in api.get(BOOKINGS, p).data['results']]  # noqa: E731
    assert ids(technician=leo.pk) == [two['id']]
    assert ids(q='wcl150') == [one['id']]
    assert ids(q='TB-2') == [two['id']]
    assert ids(since='week') == [two['id']]
    assert ids(tool=a['id']) == [one['id']]
    assert set(ids(status='out')) == {two['id']}
    assert set(ids(status='overdue')) == {one['id']}


def test_who_has_what_groups_open_items_by_person(api, lucky):
    a, b, c = (add_tool(api, name=n)['id'] for n in ('Drill', 'Grinder', 'Saw'))
    k = book(api, lucky, [a, b], date_out=days_ago(90), long_term_kit=True)
    book(api, make_staff('leonard', perms=[], first_name='Leonard'), [c])
    give_back(api, k, [k['items'][1]['id']])
    groups = api.get(f'{TOOLS}who-has-what/').data['results']
    assert [(g['technician']['name'], g['units'], g['long_term_kit']) for g in groups] == [
        ('Leonard', 1, False), ('Lucky', 1, True)]
    assert groups[1]['items'][0]['tool']['name'] == 'Drill'


def test_summary_tiles(api, lucky):
    ladder = add_tool(api, name='Ladder', quantity=4)
    drill = add_tool(api)
    add_tool(api, name='Old saw', condition='retired')
    add_tool(api, name='Lost tester', condition='lost')
    add_tool(api, name='Broken grinder', condition='in_repair')
    book(api, lucky, [(ladder['id'], 2), drill['id']])
    s = api.get(TOOLS).data['summary']
    assert (s['out_units'], s['out_people'], s['out_bookings'], s['available'], s['in_service']) == (3, 1, 1, 1, 2)
    assert (s['tools_out'], s['in_repair'], s['lost_retired']) == (2, 1, 2)


def test_tools_tile_filters(api, lucky):
    ladder = add_tool(api, name='Ladder', quantity=4)
    drill = add_tool(api)
    add_tool(api, name='Spare drill')
    add_tool(api, name='Old saw', condition='retired')
    add_tool(api, name='Lost tester', condition='lost')
    add_tool(api, name='Broken grinder', condition='in_repair')
    book(api, lucky, [(ladder['id'], 1), drill['id']])
    names = lambda **p: sorted(r['name'] for r in api.get(TOOLS, p).data['results'])  # noqa: E731
    assert names(tile='available') == ['Ladder', 'Spare drill']
    assert names(available=1) == ['Ladder', 'Spare drill']
    assert names(tile='out') == ['Hammer drill', 'Ladder']
    assert names(tile='in_repair') == ['Broken grinder']
    assert names(tile='lost_retired') == ['Lost tester', 'Old saw']


def test_booking_tiles_match_their_counts_in_either_view(api, lucky):
    a, b, c = (add_tool(api, name=n)['id'] for n in ('Drill', 'Grinder', 'Saw'))
    book(api, lucky, [a], date_out=days_ago(40))                         # out, overdue
    book(api, lucky, [b], date_out=days_ago(2), due_date=days_ago(-3))   # out, due soon
    done = book(api, lucky, [c])
    give_back(api, done)                                                 # archived
    s = api.get(BOOKINGS).data['summary']
    for tile, count in (('out', s['out_bookings']), ('overdue', s['overdue']), ('due_soon', s['due_soon']),
                        ('check', s['waiting'])):
        for view in ({}, {'archived': '1'}):
            assert api.get(BOOKINGS, {'tile': tile, **view}).data['count'] == count, (tile, view)
    assert (s['out_bookings'], s['overdue'], s['due_soon']) == (2, 1, 1)


def test_problems_tile_resolve_and_new_problems(api, lucky):
    booker = api_for(make_staff('ayesha', perms=BOOKER + ['tools.create']))
    checker = api_for(make_staff('sediek', perms=CHECKER))
    a, b, c, d = (add_tool(booker, name=n)['id'] for n in ('Drill', 'Grinder', 'Saw', 'Ladder'))
    flagged = book(booker, lucky, [a])
    checker.post(f'{BOOKINGS}check/', {'bookings': [flagged['id']], 'problem': True, 'note': 'Charger missing'},
                 format='json')
    damaged = book(booker, lucky, [b])
    give_back(booker, damaged, [{'item': damaged['items'][0]['id'], 'condition': 'damaged', 'note': 'Cracked'}])
    assert booking_obj(damaged).is_archived                     # every tool back: archived, still a problem
    fine = book(booker, lucky, [c])
    give_back(booker, fine)
    book(booker, lucky, [d])

    s = api.get(BOOKINGS).data['summary']
    assert s['problems'] == 2
    for view in ({}, {'archived': '1'}):                        # the tile ignores the archive view
        assert {r['id'] for r in api.get(BOOKINGS, {'tile': 'problems', **view}).data['results']} == \
            {flagged['id'], damaged['id']}
    row = api.get(f"{BOOKINGS}{damaged['id']}/").data['problem']
    assert row['open'] and row['items'][0]['text'] == 'Grinder: damaged (Cracked)'

    # Only a checker resolves, and needs a note.
    url = f"{BOOKINGS}{damaged['id']}/resolve/"
    assert booker.post(url, {'note': 'Sent for repair'}, format='json').status_code == 403
    assert 'note' in checker.post(url, {'note': ' '}, format='json').data
    res = checker.post(url, {'note': 'Sent for repair'}, format='json')
    assert res.status_code == 200, res.data
    assert res.data['problem']['open'] is False and res.data['problem']['resolution'] == 'Sent for repair'
    assert res.data['problem']['resolved_by']['name'] == 'Sediek'
    assert AuditLog.objects.filter(action='problem_resolved', summary='Problem resolved: Sent for repair').exists()
    assert api.get(BOOKINGS).data['summary']['problems'] == 1
    # Nothing left to resolve.
    assert 'note' in checker.post(url, {'note': 'Again'}, format='json').data

    # A new problem after the resolution counts again.
    checker.post(f"{BOOKINGS}{flagged['id']}/resolve/", {'note': 'Charger found'}, format='json')
    assert api.get(BOOKINGS).data['summary']['problems'] == 0
    give_back(booker, api.get(f"{BOOKINGS}{flagged['id']}/").data,
              [{'item': flagged['items'][0]['id'], 'condition': 'missing_parts', 'note': 'Chuck key'}])
    assert api.get(BOOKINGS).data['summary']['problems'] == 1
    assert api.get(f"{BOOKINGS}{flagged['id']}/").data['problem']['open'] is True


def test_due_soon(api, lucky):
    t = [add_tool(api, name=f'T{n}')['id'] for n in range(6)]
    today_due = book(api, lucky, [t[0]], date_out=days_ago(3), due_date=days_ago(0))
    week = book(api, lucky, [t[1]], date_out=days_ago(3), due_date=days_ago(-7))
    book(api, lucky, [t[2]], date_out=days_ago(3), due_date=days_ago(-8))   # too far off
    book(api, lucky, [t[3]], date_out=days_ago(3), due_date=days_ago(1))    # past due: overdue, not due soon
    book(api, lucky, [t[4]], date_out=days_ago(3))                          # no due date
    back = book(api, lucky, [t[5]], date_out=days_ago(3), due_date=days_ago(-2))
    give_back(api, back)                                                    # returned
    assert api.get(BOOKINGS).data['summary']['due_soon'] == 2
    ids = {r['id'] for r in api.get(BOOKINGS, {'tile': 'due_soon'}).data['results']}
    assert ids == {today_due['id'], week['id']}
    # A long-term kit has no due date, so it's never due soon.
    api.patch(f"{BOOKINGS}{week['id']}/", {'long_term_kit': True}, format='json')
    assert api.get(BOOKINGS).data['summary']['due_soon'] == 1


# ── permissions ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('method,url,perm', [
    ('post', TOOLS, 'tools.create'),
    ('patch', '{tool}', 'tools.edit'),
    ('delete', '{tool}', 'tools.delete'),
    ('post', BOOKINGS, 'tools.book'),
    ('patch', '{booking}', 'tools.book'),
    ('delete', '{booking}', 'tools.book'),
    ('post', '{booking}return/', 'tools.book'),
    ('post', f'{BOOKINGS}check/', 'tools.check'),
    ('post', '{booking}archive/', 'tools.archive'),
    ('post', f'{BOOKINGS}bulk-archive/', 'tools.archive'),
    ('patch', f'{TOOLS}settings/', 'tools.settings'),
    ('post', f'{TOOLS}types/', 'tools.settings'),
])
def test_each_action_needs_its_permission(api, lucky, method, url, perm):
    drill = add_tool(api)
    b = book(api, lucky, [drill['id']])
    url = url.format(tool=f"{TOOLS}{drill['id']}/", booking=f"{BOOKINGS}{b['id']}/")
    reader = api_for(make_staff('reader', perms=['tools.view']))
    res = getattr(reader, method)(url, {}, format='json')
    assert res.status_code == 403, res.data
    assert 'permission' in str(res.data['detail'])


def test_viewing_needs_tools_view(lucky):
    nobody = api_for(make_staff('nobody', perms=['jobs.view']))
    for url in (TOOLS, BOOKINGS, f'{TOOLS}options/', f'{TOOLS}who-has-what/', f'{TOOLS}settings/'):
        assert nobody.get(url).status_code == 403, url
    reader = api_for(make_staff('reader', perms=['tools.view']))
    for url in (TOOLS, BOOKINGS, f'{TOOLS}options/', f'{TOOLS}who-has-what/', f'{TOOLS}settings/'):
        assert reader.get(url).status_code == 200, url
    assert reader.get(f'{TOOLS}options/').data['can_check'] is False


def test_clients_have_no_access(db):
    from ..client_portal.conftest import make_client
    assert api_for(make_client('acme')).get(TOOLS).status_code == 403


# ── history ──────────────────────────────────────────────────────────────────

def test_a_tools_history_includes_its_bookings(api, lucky):
    drill = add_tool(api)
    other = add_tool(api, name='Grinder')
    b = book(api, lucky, [drill['id']])
    book(api, lucky, [other['id']])
    give_back(api, b)
    rows = api.get(f"/records/history/tools.tool/{drill['id']}/").data
    actions = [(r['object_type'], r['action']) for r in rows]
    assert ('tools.tool', 'created') in actions
    assert ('tools.booking', 'booked_out') in actions and ('tools.booking', 'returned') in actions
    assert ('tools.booking', 'archived') in actions
    # The grinder's booking isn't in the drill's history.
    assert all(r['object_repr'].startswith('TB-0001') for r in rows if r['object_type'] == 'tools.booking')
    labels = {r['action']: r['action_label'] for r in rows}
    assert labels['booked_out'] == 'Booked out'


def test_trash_previews_and_restore(api, lucky):
    drill = add_tool(api)
    b = book(api, lucky, [drill['id']])
    api.delete(f"{BOOKINGS}{b['id']}/")
    preview = api.get(f"/records/trash/tools.booking/{b['id']}/").data
    assert preview['detail']['kind'] == 'tool_booking' and preview['detail']['technician'] == 'Lucky'
    assert preview['restore_blocked'] == ''
    api.delete(f"{TOOLS}{drill['id']}/")
    preview = api.get(f"/records/trash/tools.tool/{drill['id']}/").data
    assert preview['detail']['kind'] == 'tool' and preview['detail']['tool_id'] == 'TL-001'
    # The booking can't come back before its tool.
    assert 'Restore the tool first' in api.get(f"/records/trash/tools.booking/{b['id']}/").data['restore_blocked']
    assert api.post(f"/records/trash/tools.tool/{drill['id']}/restore/").status_code == 200
    assert api.post(f"/records/trash/tools.booking/{b['id']}/restore/").status_code == 200
    assert api.get(f"{TOOLS}{drill['id']}/").data['availability'] == 'out'


def test_movement_rows(api, lucky):
    drill = add_tool(api)
    b = book(api, lucky, [drill['id']])
    give_back(api, b)
    data = api.get(f"{BOOKINGS}{b['id']}/").data
    assert [m['kind'] for m in data['movements']] == ['out', 'return']
    assert all(m['checked'] for m in data['movements'])  # the superuser can check
    assert Movement.objects.filter(booking_id=b['id'], checked_by__username='boss').count() == 2
