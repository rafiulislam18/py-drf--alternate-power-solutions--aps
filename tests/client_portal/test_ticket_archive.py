"""
Archiving tickets: completed / cancelled archives automatically, reopening an
auto-archived ticket brings it back, archive / unarchive / bulk need
tickets.archive, lists and summaries show active tickets unless asked, chat
never unarchives, and clients see (only) their own archived tickets but can't
archive anything.
"""

import pytest
from django.contrib.auth.models import User

from apps.client_portal.models import Ticket
from apps.core.models import ClientProfile
from apps.records.models import AuditLog
from apps.staff.models import StaffAccess

from .conftest import api_for, make_ticket

pytestmark = pytest.mark.django_db
STAFF = '/client-portal/staff/tickets/'
CLIENT = '/client-portal/tickets/'


def make_staff(username, perms):
    user = User.objects.create_user(username, password='x')
    ClientProfile.objects.create(user=user, role='admin')
    StaffAccess.objects.create(user=user, permissions=perms)
    return user


@pytest.fixture
def staff(admin_user):
    return api_for(admin_user)


def set_status(api, ticket, status, **extra):
    res = api.patch(f'{STAFF}{ticket.pk}/', {'status': status, 'notify_client': False, **extra}, format='json')
    assert res.status_code == 200, res.data
    return res.data


def is_archived(ticket):
    return Ticket.objects.get(pk=ticket.pk).is_archived


def actions(ticket):
    return list(AuditLog.objects.filter(object_type='client_portal.ticket', object_id=str(ticket.pk))
                .order_by('id').values_list('action', 'summary'))


# ── the auto rule ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('status', ['completed', 'cancelled'])
def test_completing_or_cancelling_archives(staff, client_user, site, status):
    ticket = make_ticket(client_user, site, status='in_progress')
    res = set_status(staff, ticket, status)
    assert res['archive_event'] == {'action': 'archived', 'automatic': True, 'note': status}
    assert res['archive_reason'] == 'auto' and res['archived_by'] is not None
    assert actions(ticket)[-1] == ('archived', f'Archived automatically: {status}')


def test_other_statuses_and_technician_changes_dont_archive(staff, client_user, site):
    ticket = make_ticket(client_user, site)
    assert set_status(staff, ticket, 'on_site')['archive_event'] is None
    res = staff.patch(f'{STAFF}{ticket.pk}/', {'technician_name': 'Themba'}, format='json')
    assert res.data['archive_event'] is None and not is_archived(ticket)


def test_reopening_an_auto_archived_ticket_brings_it_back(staff, client_user, site):
    ticket = make_ticket(client_user, site)
    set_status(staff, ticket, 'completed')
    res = set_status(staff, ticket, 'in_progress')
    assert res['archive_event'] == {'action': 'unarchived', 'automatic': True,
                                    'note': 'status changed to In progress'}
    assert not is_archived(ticket)


def test_completed_to_cancelled_stays_archived(staff, client_user, site):
    ticket = make_ticket(client_user, site)
    set_status(staff, ticket, 'completed')
    assert set_status(staff, ticket, 'cancelled')['archive_event'] is None
    ticket.refresh_from_db()
    assert ticket.is_archived and ticket.archive_note == 'completed'


def test_a_ticket_archived_by_hand_stays_archived_when_reopened(staff, client_user, site):
    ticket = make_ticket(client_user, site, status='completed')
    assert staff.post(f'{STAFF}{ticket.pk}/archive/').status_code == 200
    assert set_status(staff, ticket, 'open')['archive_event'] is None
    assert is_archived(ticket)


def test_undo_keeps_a_completed_ticket_active(staff, client_user, site):
    ticket = make_ticket(client_user, site)
    set_status(staff, ticket, 'completed')
    assert staff.post(f'{STAFF}{ticket.pk}/unarchive/').status_code == 200
    assert set_status(staff, ticket, 'cancelled')['archive_event'] is None
    assert not is_archived(ticket)


def test_chat_messages_dont_unarchive(staff, api, client_user, site):
    ticket = make_ticket(client_user, site)
    set_status(staff, ticket, 'completed')
    assert api.post(f'{CLIENT}{ticket.pk}/messages/', {'body': 'Thanks!'}, format='json').status_code == 201
    assert staff.post(f'{STAFF}{ticket.pk}/messages/', {'body': 'Pleasure'}, format='json').status_code == 201
    assert is_archived(ticket)


def test_existing_completed_tickets_are_not_archived(client_user, site):
    ticket = make_ticket(client_user, site, status='completed')
    assert not is_archived(ticket)


# ── archive / unarchive / bulk ───────────────────────────────────────────────

def test_archive_needs_the_permission(client_user, site):
    ticket = make_ticket(client_user, site)
    viewer = api_for(make_staff('viewer', ['tickets.view', 'tickets.edit']))
    assert viewer.post(f'{STAFF}{ticket.pk}/archive/').status_code == 403
    assert viewer.post(f'{STAFF}bulk-archive/', {'ids': [ticket.pk], 'archived': True},
                       format='json').status_code == 403
    archiver = api_for(make_staff('archiver', ['tickets.view', 'tickets.archive']))
    res = archiver.post(f'{STAFF}{ticket.pk}/archive/')
    assert res.status_code == 200 and res.data['archive_reason'] == 'manual'
    assert archiver.post(f'{STAFF}{ticket.pk}/unarchive/').data['archived_at'] is None
    assert [a for a, _ in actions(ticket)] == ['archived', 'unarchived']


def test_status_change_archives_without_the_archive_permission(client_user, site):
    ticket = make_ticket(client_user, site)
    editor = api_for(make_staff('editor', ['tickets.view', 'tickets.edit']))
    res = editor.patch(f'{STAFF}{ticket.pk}/', {'status': 'completed', 'notify_client': False}, format='json')
    assert res.status_code == 200 and res.data['archive_event']['action'] == 'archived'


def test_bulk_archive(staff, client_user, site):
    a, b = make_ticket(client_user, site), make_ticket(client_user, site)
    res = staff.post(f'{STAFF}bulk-archive/', {'ids': [a.pk, b.pk, 999999], 'archived': True}, format='json')
    assert res.data == {'done': [a.pk, b.pk], 'skipped': [999999]}
    res = staff.post(f'{STAFF}bulk-archive/', {'ids': [b.pk], 'archived': False}, format='json')
    assert res.data == {'done': [b.pk], 'skipped': []}
    assert is_archived(a) and not is_archived(b)


def test_clients_cant_archive(api, client_user, site):
    ticket = make_ticket(client_user, site)
    assert api.post(f'{STAFF}{ticket.pk}/archive/').status_code == 403
    assert api.post(f'{STAFF}bulk-archive/', {'ids': [ticket.pk], 'archived': True}, format='json').status_code == 403
    assert api.post(f'{CLIENT}{ticket.pk}/archive/').status_code == 404
    assert not is_archived(ticket)


# ── lists and summaries ──────────────────────────────────────────────────────

@pytest.fixture
def mixed(staff, client_user, site, other_client, other_site):
    live = make_ticket(client_user, site, title='Geyser leak')
    done = make_ticket(client_user, site, title='Old geyser', status='in_progress')
    set_status(staff, done, 'completed')
    theirs = make_ticket(other_client, other_site, title='Their old roof')
    set_status(staff, theirs, 'cancelled')
    return live, done, theirs


def ids(res):
    return [t['id'] for t in res.data['results']]


def test_staff_lists_show_active_tickets_unless_asked(staff, mixed):
    live, done, theirs = mixed
    assert ids(staff.get(STAFF)) == [live.pk]
    assert set(ids(staff.get(STAFF, {'archived': 1}))) == {done.pk, theirs.pk}
    assert set(ids(staff.get(STAFF, {'search': 'geyser', 'include_archived': 1}))) == {live.pk, done.pk}
    assert ids(staff.get(STAFF, {'search': done.reference})) == []
    row = staff.get(STAFF, {'search': done.reference, 'include_archived': 1}).data['results'][0]
    assert row['archived_at'] and row['archive_note'] == 'completed' and row['archived_by']['id']


def test_staff_summary_follows_the_view(staff, mixed, client_user):
    summary = staff.get(f'{STAFF}summary/').data
    assert summary == {'open': 1, 'in_progress': 0, 'waiting': 0, 'completed': 0, 'all': 1, 'archived_count': 2}
    archived = staff.get(f'{STAFF}summary/', {'archived': 1}).data
    assert archived['completed'] == archived['all'] == 2 and archived['archived_count'] == 2
    one_client = staff.get(f'{STAFF}summary/', {'client': client_user.pk, 'archived': 1}).data
    assert one_client['all'] == 1 and one_client['archived_count'] == 1


def test_clients_see_only_their_own_archived_tickets(api, mixed):
    live, done, theirs = mixed
    assert ids(api.get(CLIENT)) == [live.pk]
    assert ids(api.get(CLIENT, {'archived': 1})) == [done.pk]
    assert set(ids(api.get(CLIENT, {'include_archived': 1}))) == {live.pk, done.pk}
    row = api.get(CLIENT, {'archived': 1}).data['results'][0]
    assert row['archived_at'] and row['archive_note'] == 'completed' and 'archived_by' not in row
    assert api.get(f'{CLIENT}{done.pk}/').status_code == 200  # still viewable
    summary = api.get(f'{CLIENT}summary/').data
    assert summary['all'] == 1 and summary['archived_count'] == 1
    assert api.get(f'{CLIENT}summary/', {'archived': 1}).data['completed'] == 1
