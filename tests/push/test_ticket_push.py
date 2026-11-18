"""
Tests for ticket chat push notifications (apps.push.tickets): who gets them,
what they say, skipping people who have the chat open (their own read marker),
bursts, and that they're queued after the commit with the short delay.
"""

from unittest.mock import patch

import pytest

from apps.client_portal.models import TicketChatRead, TicketMessage
from apps.push.models import PushSubscription
from apps.push.tickets import PUSH_DELAY_SECONDS, send_ticket_message_push
from tests.client_portal.conftest import api_for, make_client, make_ticket

from .conftest import aps_device, endpoint, make_staff, payloads


@pytest.fixture
def people(db, admin_user):
    client = make_client('acme', email='sipho@acme.example', company_name='Acme Properties')
    return {
        'client': client,
        'other_client': make_client('otherco', company_name='Other Co'),
        'super': admin_user,
        'reader': make_staff('reader', ['tickets.view', 'tickets.chat_view', 'tickets.chat_send'], first_name='Thabo'),
        'no_chat': make_staff('nochat', ['tickets.view']),
    }


@pytest.fixture
def devices(people):
    """One device each; endpoint number = position here."""
    order = ['client', 'other_client', 'super', 'reader', 'no_chat']
    return {name: aps_device(people[name], n) for n, name in enumerate(order)}


@pytest.fixture
def ticket(people):
    from apps.solar_dashboard.models import Site
    site = Site.objects.create(client=people['client'], name='Head office')
    return make_ticket(people['client'], site, title='Geyser leak')


def client_url(t):
    return f'/client-portal/tickets/{t.pk}/messages/'


def staff_url(t):
    return f'/client-portal/staff/tickets/{t.pk}/messages/'


def post(api, url, body, capture, execute=True):
    with capture(execute=execute):
        res = api.post(url, {'body': body}, format='json')
    assert res.status_code == 201, res.content
    return TicketMessage.objects.get(pk=res.json()['message']['id'])


def sent_to(mock):
    return sorted(url for url, _ in payloads(mock))


# ── who gets it ─────────────────────────────────────────────────────────────


def test_client_message_goes_to_staff_who_can_read_chats(sent, people, devices, ticket,
                                                          django_capture_on_commit_callbacks):
    post(api_for(people['client']), client_url(ticket), 'Water everywhere', django_capture_on_commit_callbacks)
    # Superuser (2) and the staff member with tickets.chat_view (3); not the
    # sender (0), another client (1) or staff without chat access (4).
    assert sent_to(sent) == [endpoint(2), endpoint(3)]


def test_staff_message_goes_to_the_tickets_client_only(sent, people, devices, ticket,
                                                       django_capture_on_commit_callbacks):
    post(api_for(people['reader']), staff_url(ticket), 'On our way', django_capture_on_commit_callbacks)
    assert sent_to(sent) == [endpoint(0)]


def test_never_the_sender(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    post(api_for(people['super']), staff_url(ticket), 'Hi', django_capture_on_commit_callbacks)
    assert endpoint(2) not in sent_to(sent)


def test_inactive_accounts_skipped(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    people['reader'].is_active = False
    people['reader'].save()
    post(api_for(people['client']), client_url(ticket), 'Hello?', django_capture_on_commit_callbacks)
    assert sent_to(sent) == [endpoint(2)]


def test_every_device_of_a_recipient(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    aps_device(people['client'], 99)
    post(api_for(people['reader']), staff_url(ticket), 'Update', django_capture_on_commit_callbacks)
    assert sent_to(sent) == sorted([endpoint(0), endpoint(99)])


# ── what it says ────────────────────────────────────────────────────────────


def test_content_from_client(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    post(api_for(people['client']), client_url(ticket), 'Water   everywhere\nplease help', django_capture_on_commit_callbacks)
    _, payload = payloads(sent)[0]
    assert payload == {
        'app': 'aps',
        'title': f'New message on {ticket.reference}',
        'body': 'Acme Properties: Water everywhere please help',
        'tag': f'aps-ticket-{ticket.pk}',
        'url': f'/dashboard/tickets/{ticket.pk}',
    }


def test_content_from_staff_uses_first_name(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    post(api_for(people['reader']), staff_url(ticket), 'On our way', django_capture_on_commit_callbacks)
    assert payloads(sent)[0][1]['body'] == 'Thabo · APS: On our way'


def test_long_message_shortened(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    post(api_for(people['client']), client_url(ticket), 'x' * 500, django_capture_on_commit_callbacks)
    body = payloads(sent)[0][1]['body']
    assert body.endswith('…')
    assert len(body) == len('Acme Properties: ') + 120


# ── chat already open → skipped ─────────────────────────────────────────────


def test_client_with_chat_open_is_skipped(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    msg = post(api_for(people['reader']), staff_url(ticket), 'On our way', django_capture_on_commit_callbacks,
               execute=False)
    # The poke makes the open (visible) chat fetch with read=1 before the push runs.
    api_for(people['client']).get(client_url(ticket), {'after': 0, 'read': 1})
    assert send_ticket_message_push(msg.pk) is None
    sent.assert_not_called()


def test_only_the_staff_member_with_chat_open_is_skipped(sent, people, devices, ticket,
                                                         django_capture_on_commit_callbacks):
    msg = post(api_for(people['client']), client_url(ticket), 'Hello', django_capture_on_commit_callbacks,
               execute=False)
    api_for(people['reader']).get(staff_url(ticket), {'after': 0, 'read': 1})
    send_ticket_message_push(msg.pk)
    assert sent_to(sent) == [endpoint(2)]  # the superuser still gets it


def test_fetch_without_read_doesnt_count_as_open(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    # A background tab fetches without read=1: the push still goes.
    msg = post(api_for(people['reader']), staff_url(ticket), 'Hi', django_capture_on_commit_callbacks, execute=False)
    api_for(people['client']).get(client_url(ticket), {'after': 0})
    send_ticket_message_push(msg.pk)
    assert sent_to(sent) == [endpoint(0)]


def test_read_marker_is_per_person_and_only_moves_forward(people, ticket, django_capture_on_commit_callbacks):
    first = post(api_for(people['client']), client_url(ticket), 'One', django_capture_on_commit_callbacks)
    second = post(api_for(people['client']), client_url(ticket), 'Two', django_capture_on_commit_callbacks)
    reader = api_for(people['reader'])
    reader.get(staff_url(ticket), {'after': 0, 'read': 1})
    reader.get(staff_url(ticket), {'after': 0, 'read': 1})
    reader.get(staff_url(ticket), {'after': first.pk - 1, 'read': 1})  # an older page must not move it back
    assert TicketChatRead.objects.get(user=people['reader'], ticket=ticket).read_upto == second.pk
    assert not TicketChatRead.objects.filter(user=people['super']).exists()


# ── bursts, timing, queue ───────────────────────────────────────────────────


def test_burst_sends_only_the_newest(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    api = api_for(people['client'])
    first = post(api, client_url(ticket), 'One', django_capture_on_commit_callbacks, execute=False)
    second = post(api, client_url(ticket), 'Two', django_capture_on_commit_callbacks, execute=False)
    assert send_ticket_message_push(first.pk) is None
    send_ticket_message_push(second.pk)
    assert {p['body'] for _, p in payloads(sent)} == {'Acme Properties: Two'}


def test_a_reply_from_the_other_side_doesnt_cancel_it(sent, people, devices, ticket,
                                                      django_capture_on_commit_callbacks):
    msg = post(api_for(people['client']), client_url(ticket), 'Help', django_capture_on_commit_callbacks, execute=False)
    post(api_for(people['reader']), staff_url(ticket), 'Coming', django_capture_on_commit_callbacks, execute=False)
    send_ticket_message_push(msg.pk)
    assert endpoint(2) in sent_to(sent)


def test_queued_after_commit_with_short_delay(people, ticket, django_capture_on_commit_callbacks):
    with patch('apps.push.tasks.push_ticket_message.apply_async') as queued:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            res = api_for(people['client']).post(client_url(ticket), {'body': 'Hi'}, format='json')
        queued.assert_not_called()  # nothing before the commit
        for callback in callbacks:
            callback()
    queued.assert_called_once_with(args=[res.json()['message']['id']], countdown=PUSH_DELAY_SECONDS)


def test_celery_down_sends_straight_away(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    with patch('apps.push.tasks.push_ticket_message.apply_async', side_effect=ConnectionError('no broker')):
        post(api_for(people['reader']), staff_url(ticket), 'Hi', django_capture_on_commit_callbacks)
    assert sent_to(sent) == [endpoint(0)]


def test_push_failure_never_breaks_sending_a_message(people, devices, ticket, django_capture_on_commit_callbacks,
                                                     vapid):
    with patch('apps.push.send.webpush', side_effect=RuntimeError('boom')):
        post(api_for(people['reader']), staff_url(ticket), 'Hi', django_capture_on_commit_callbacks)
    assert PushSubscription.objects.count() == 5


def test_deleted_message_sends_nothing(sent, people, devices, ticket, django_capture_on_commit_callbacks):
    msg = post(api_for(people['client']), client_url(ticket), 'Hi', django_capture_on_commit_callbacks, execute=False)
    ticket.delete()
    assert send_ticket_message_push(msg.pk) is None
    sent.assert_not_called()


def test_no_devices_no_send(sent, people, ticket, django_capture_on_commit_callbacks):
    post(api_for(people['client']), client_url(ticket), 'Hi', django_capture_on_commit_callbacks)
    sent.assert_not_called()
