"""
Tests for the 30-minute unread ticket-chat emails (apps.client_portal.digests):
one email per client and one for the team, read messages skipped, nothing sent
twice, fresh messages held back, confirmed emails only, SMTP failure retried,
overlapping runs blocked, and the Celery Beat schedule.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from apps.client_portal import digests
from apps.client_portal.models import Ticket, TicketMessage
from apps.solar_dashboard.models import Site

from .conftest import make_client, make_ticket

LATER = timezone.now() + timedelta(minutes=31)  # "now" for runs: messages have been unread 30+ minutes


@pytest.fixture(autouse=True)
def _team(settings):
    settings.EMAIL_RECIPIENT = 'team@aps.example'


def say(ticket, role, body, author=None, age=None):
    m = TicketMessage.objects.create(ticket=ticket, author=author, author_role=role, body=body)
    if age is not None:
        TicketMessage.objects.filter(pk=m.pk).update(created_at=timezone.now() - age)
    return m


def run(now=LATER):
    return digests.send_chat_digests(now=now)


@pytest.fixture
def ticket(client_user, site):
    return make_ticket(client_user, site, title='Geyser leak')


def test_nothing_to_send(db):
    assert run() == {'skipped': False, 'clients_emailed': 0, 'team_emailed': False}
    assert mail.outbox == []


def test_client_gets_one_email_for_all_unread_tickets(client_user, site, ticket, admin_user):
    admin_user.first_name = 'Thabo'
    admin_user.save()
    second = make_ticket(client_user, site, title='DB board trips')
    say(ticket, 'staff', 'Visit at 14:00', admin_user)
    say(ticket, 'staff', 'Bring the key', admin_user)
    say(second, 'staff', 'Quote attached <b>soon</b>', admin_user)

    assert run()['clients_emailed'] == 1
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ['sipho@acme.example']
    assert msg.subject == '3 new messages on your APS tickets'
    for text in ('Visit at 14:00', 'Bring the key', ticket.reference, second.reference, 'Thabo · APS',
                 f'/dashboard/tickets/{ticket.pk}'):
        assert text in msg.body
    assert '<b>soon</b>' not in msg.body


def test_single_ticket_subject(ticket):
    say(ticket, 'staff', 'Hello')
    run()
    assert mail.outbox[0].subject == f'New message on ticket {ticket.reference}: Geyser leak'
    assert 'APS team' in mail.outbox[0].body  # no staff author → never a username


def test_team_gets_one_email_for_all_clients(ticket, other_client, other_site):
    other = make_ticket(other_client, other_site, title='Roof paint')
    say(ticket, 'client', 'Any update?')
    say(other, 'client', 'When can you come?')
    res = run()
    assert res['team_emailed'] is True and res['clients_emailed'] == 0
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ['team@aps.example']
    assert msg.subject == '2 unread client messages on 2 tickets'
    assert 'Acme Properties' in msg.body and 'Other Co' in msg.body


def test_read_messages_are_not_emailed(ticket):
    m = say(ticket, 'staff', 'Seen already')
    Ticket.objects.filter(pk=ticket.pk).update(client_read_upto=m.pk)
    assert run()['clients_emailed'] == 0
    assert mail.outbox == []


def test_never_emailed_twice_but_new_ones_are(ticket):
    say(ticket, 'staff', 'First')
    run()
    run()
    assert len(mail.outbox) == 1
    say(ticket, 'staff', 'Second')
    run()
    assert len(mail.outbox) == 2
    assert 'Second' in mail.outbox[1].body and 'First' not in mail.outbox[1].body


def test_messages_wait_30_minutes_unread(ticket):
    say(ticket, 'staff', 'Just now')
    assert run(now=timezone.now())['clients_emailed'] == 0  # younger than MIN_AGE
    assert run(now=timezone.now() + timedelta(minutes=29))['clients_emailed'] == 0
    assert digests.MIN_AGE == timedelta(minutes=30)
    assert mail.outbox == []
    assert run()['clients_emailed'] == 1


def test_own_side_messages_ignored(ticket):
    say(ticket, 'client', 'From the client')
    run()
    assert [m.to for m in mail.outbox] == [['team@aps.example']]


def test_unconfirmed_client_email_is_skipped_and_marked(db):
    newco = make_client('newco', email='x@new.example', company_name='NewCo', verified=False)
    t = make_ticket(newco, Site.objects.create(client=newco, name='A'))
    m = say(t, 'staff', 'Hello')
    assert run()['clients_emailed'] == 0
    assert mail.outbox == []
    t.refresh_from_db()
    assert t.client_emailed_upto == m.pk  # not resent later out of the blue


def test_smtp_failure_is_retried_next_run(ticket, monkeypatch):
    say(ticket, 'staff', 'Hello')
    monkeypatch.setattr(digests, 'send_client_chat_digest', lambda *a: False)
    assert run()['clients_emailed'] == 0
    monkeypatch.undo()
    assert run()['clients_emailed'] == 1


def test_team_email_needs_recipient(settings, ticket):
    settings.EMAIL_RECIPIENT = ''
    say(ticket, 'client', 'Hi')
    assert run()['team_emailed'] is False
    ticket.refresh_from_db()
    assert ticket.staff_emailed_upto == 0  # sent once a recipient is configured


def test_overlapping_run_is_skipped(ticket):
    say(ticket, 'staff', 'Hello')
    cache.add(digests.LOCK_KEY, 1, 60)
    try:
        assert run()['skipped'] is True
        assert mail.outbox == []
    finally:
        cache.delete(digests.LOCK_KEY)
    assert run()['clients_emailed'] == 1


def test_long_conversation_is_trimmed(ticket):
    for i in range(8):
        say(ticket, 'staff', f'Message {i}')
    run()
    body = mail.outbox[0].body
    assert 'Message 7' in body and 'Message 0' not in body and '3 earlier messages' in body


def test_management_command(ticket):
    say(ticket, 'client', 'Hi', age=timedelta(minutes=31))
    call_command('send_chat_digests')
    assert len(mail.outbox) == 1


def test_schedule_migration_targets_the_task_every_30_minutes():
    # Test settings don't install django_celery_beat, so check the migration itself.
    import importlib

    from apps.client_portal.tasks import send_ticket_chat_digests

    mig = importlib.import_module('apps.client_portal.migrations.0009_chat_digest_schedule')
    assert mig.TASK_PATH == f'{send_ticket_chat_digests.__module__}.{send_ticket_chat_digests.__name__}'
    import inspect
    src = inspect.getsource(mig.create_schedule)
    assert "minute='0,30'" in src and "hour='*'" in src


def test_schedule_now_checks_every_5_minutes():
    import importlib
    import inspect

    mig = importlib.import_module('apps.client_portal.migrations.0014_chat_digest_every_5_min')
    assert mig.TASK_PATH == 'apps.client_portal.tasks.send_ticket_chat_digests'
    assert "'*/5'" in inspect.getsource(mig.every_5)
