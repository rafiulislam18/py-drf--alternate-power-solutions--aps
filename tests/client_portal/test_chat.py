"""
Tests for ticket chat (client ↔ APS staff): who may read/write, incremental
polling with ?after=, read markers (unread badges + "Seen"), the live ticket
status in each poll, the list/detail unread counts, and that sending a message
emails nobody straight away (the 30-minute digest does — test_chat_digests.py).
"""

import pytest
from django.core import mail

from apps.client_portal.models import TicketMessage

from .conftest import api_for, make_client, make_ticket


@pytest.fixture
def ticket(client_user, site):
    return make_ticket(client_user, site, title='Geyser leak')


@pytest.fixture
def staff(admin_user):
    return api_for(admin_user)


def client_url(t):
    return f'/client-portal/tickets/{t.pk}/messages/'


def staff_url(t):
    return f'/client-portal/staff/tickets/{t.pk}/messages/'


def post(api, url, body, capture):
    with capture(execute=True):
        return api.post(url, {'body': body}, format='json')


# ── access ──────────────────────────────────────────────────────────────────


def test_requires_sign_in(ticket):
    assert api_for().get(client_url(ticket)).status_code == 401
    assert api_for().get(staff_url(ticket)).status_code == 401


def test_other_client_gets_404(ticket, other_client):
    api = api_for(other_client)
    assert api.get(client_url(ticket)).status_code == 404
    assert api.post(client_url(ticket), {'body': 'hi'}, format='json').status_code == 404
    assert not TicketMessage.objects.exists()


def test_client_cannot_use_staff_endpoint_and_staff_not_client_one(api, staff, ticket):
    assert api.get(staff_url(ticket)).status_code == 403
    assert staff.get(client_url(ticket)).status_code == 403


# ── conversation ────────────────────────────────────────────────────────────


def test_conversation_both_ways(api, staff, ticket, admin_user, django_capture_on_commit_callbacks):
    admin_user.first_name = 'Thabo'
    admin_user.save()
    res = post(api, client_url(ticket), '  Is someone coming today?\n\nThanks  ', django_capture_on_commit_callbacks)
    assert res.status_code == 201, res.data
    assert res.data['message']['body'] == 'Is someone coming today?\n\nThanks'
    assert res.data['message']['mine'] is True and res.data['message']['author_name'] == 'Acme Properties'

    res = post(staff, staff_url(ticket), 'Yes, Thabo at 14:00.', django_capture_on_commit_callbacks)
    assert res.status_code == 201

    msgs = api.get(client_url(ticket)).data['messages']
    assert [(m['author_role'], m['mine']) for m in msgs] == [('client', True), ('staff', False)]
    assert msgs[1]['author_name'] == 'Thabo · APS'
    staff_view = staff.get(staff_url(ticket)).data['messages']
    assert [m['mine'] for m in staff_view] == [False, True]


def test_after_returns_only_new_messages(api, staff, ticket, django_capture_on_commit_callbacks):
    first = post(api, client_url(ticket), 'one', django_capture_on_commit_callbacks).data['message']['id']
    post(staff, staff_url(ticket), 'two', django_capture_on_commit_callbacks)
    res = api.get(client_url(ticket), {'after': first})
    assert [m['body'] for m in res.data['messages']] == ['two']
    last = res.data['messages'][-1]['id']
    assert api.get(client_url(ticket), {'after': last}).data['messages'] == []


def test_poll_carries_live_status(api, staff, ticket):
    staff.patch(f'/client-portal/staff/tickets/{ticket.pk}/',
                {'status': 'visit_booked', 'technician_name': 'Thabo', 'notify_client': False}, format='json')
    t = api.get(client_url(ticket)).data['ticket']
    assert t['status'] == 'visit_booked' and t['status_label'] == 'Visit booked' and t['technician_name'] == 'Thabo'


@pytest.mark.parametrize('body', ['', '   \n  ', 'x' * 4001])
def test_bad_body_rejected_with_field_error(api, ticket, body):
    res = api.post(client_url(ticket), {'body': body}, format='json')
    assert res.status_code == 400
    assert 'body' in res.data


def test_message_bumps_ticket_last_update(api, ticket, django_capture_on_commit_callbacks):
    before = ticket.updated_at
    post(api, client_url(ticket), 'hello', django_capture_on_commit_callbacks)
    ticket.refresh_from_db()
    assert ticket.updated_at > before


# ── read markers ────────────────────────────────────────────────────────────


def test_unread_counts_and_seen(api, staff, ticket, django_capture_on_commit_callbacks):
    post(staff, staff_url(ticket), 'a', django_capture_on_commit_callbacks)
    post(staff, staff_url(ticket), 'b', django_capture_on_commit_callbacks)

    row = api.get('/client-portal/tickets/').data['results'][0]
    assert row['unread_count'] == 2
    assert api.get(f'/client-portal/tickets/{ticket.pk}/').data['unread_count'] == 2

    # Polling without read=1 (tab hidden) doesn't mark anything seen.
    api.get(client_url(ticket))
    assert api.get('/client-portal/tickets/').data['results'][0]['unread_count'] == 2
    # Staff don't see "Seen" yet.
    assert staff.get(staff_url(ticket)).data['other_read_upto'] == 0

    msgs = api.get(client_url(ticket), {'read': '1'}).data['messages']
    assert api.get('/client-portal/tickets/').data['results'][0]['unread_count'] == 0
    assert staff.get(staff_url(ticket)).data['other_read_upto'] == msgs[-1]['id']


def test_own_messages_never_count_as_unread(api, staff, ticket, django_capture_on_commit_callbacks):
    post(api, client_url(ticket), 'from client', django_capture_on_commit_callbacks)
    assert api.get('/client-portal/tickets/').data['results'][0]['unread_count'] == 0
    staff_row = staff.get('/client-portal/staff/tickets/').data['results'][0]
    assert staff_row['unread_count'] == 1
    staff.get(staff_url(ticket), {'read': '1'})
    assert staff.get('/client-portal/staff/tickets/').data['results'][0]['unread_count'] == 0


def test_unread_count_not_multiplied_by_attachments(api, staff, ticket, django_capture_on_commit_callbacks):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.client_portal.models import TicketAttachment
    for i in range(3):
        TicketAttachment.objects.create(ticket=ticket, file=SimpleUploadedFile(f'{i}.png', b'x'),
                                        original_name=f'{i}.png', size=1)
    post(staff, staff_url(ticket), 'a', django_capture_on_commit_callbacks)
    post(staff, staff_url(ticket), 'b', django_capture_on_commit_callbacks)
    row = api.get('/client-portal/tickets/').data['results'][0]
    assert (row['unread_count'], row['attachment_count']) == (2, 3)


# ── emails ──────────────────────────────────────────────────────────────────


def test_sending_a_message_emails_nobody_immediately(api, staff, ticket, django_capture_on_commit_callbacks):
    post(staff, staff_url(ticket), 'Visit at 14:00', django_capture_on_commit_callbacks)
    post(api, client_url(ticket), 'Thanks', django_capture_on_commit_callbacks)
    assert mail.outbox == []


def test_message_throttle(monkeypatch, api, ticket, django_capture_on_commit_callbacks):
    from apps.client_portal.throttling import MessageWriteThrottle
    monkeypatch.setattr(MessageWriteThrottle, 'rate', '3/h', raising=False)
    codes = [post(api, client_url(ticket), f'm{i}', django_capture_on_commit_callbacks).status_code for i in range(4)]
    assert codes == [201, 201, 201, 429]
    # Polling is never throttled.
    assert all(api.get(client_url(ticket)).status_code == 200 for _ in range(5))


# ── sidebar unread totals ───────────────────────────────────────────────────


def test_unread_totals_for_the_sidebar_dot(api, staff, ticket, client_user, site, django_capture_on_commit_callbacks):
    second = make_ticket(client_user, site, title='Second')
    assert api.get('/client-portal/tickets/unread/').data == {'messages': 0, 'tickets': 0}
    post(staff, staff_url(ticket), 'a', django_capture_on_commit_callbacks)
    post(staff, staff_url(ticket), 'b', django_capture_on_commit_callbacks)
    post(staff, staff_url(second), 'c', django_capture_on_commit_callbacks)
    assert api.get('/client-portal/tickets/unread/').data == {'messages': 3, 'tickets': 2}
    api.get(client_url(ticket), {'read': '1'})
    assert api.get('/client-portal/tickets/unread/').data == {'messages': 1, 'tickets': 1}
    # Staff: the client's replies.
    assert staff.get('/client-portal/staff/tickets/unread/').data == {'messages': 0, 'tickets': 0}
    post(api, client_url(ticket), 'thanks', django_capture_on_commit_callbacks)
    assert staff.get('/client-portal/staff/tickets/unread/').data == {'messages': 1, 'tickets': 1}


def test_unread_totals_are_scoped(api, staff, other_client, other_site, django_capture_on_commit_callbacks):
    theirs = make_ticket(other_client, other_site)
    post(staff, staff_url(theirs), 'for them', django_capture_on_commit_callbacks)
    assert api.get('/client-portal/tickets/unread/').data['messages'] == 0
    assert api.get('/client-portal/staff/tickets/unread/').status_code == 403
    assert staff.get('/client-portal/tickets/unread/').status_code == 403
