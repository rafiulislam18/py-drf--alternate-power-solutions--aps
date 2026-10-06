"""
Tests for the new-ticket emails (apps.client_portal.alerts): normal and urgent
tickets wait for the 30-minute job, emergencies go at once; one team email per
run, a confirmation per client ticket (confirmed emails only), nothing sent
twice, tickets the team already moved on are skipped, SMTP failures retried,
overlapping runs blocked, and the Celery Beat schedule.
"""

import importlib
import inspect
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command

from apps.client_portal import alerts
from apps.client_portal.models import Ticket

from .conftest import api_for, make_client, make_service, make_ticket
from apps.solar_dashboard.models import Site

TICKETS_URL = '/client-portal/tickets/'


@pytest.fixture(autouse=True)
def _team(settings):
    settings.EMAIL_RECIPIENT = 'team@aps.example'


def raise_ticket(api, site, capture, **kw):
    data = {
        'service': make_service('Electrical').pk,
        'title': 'DB board tripping',
        'description': 'Trips when the aircon starts.',
        'site': site.pk,
        'urgency': 'normal',
    }
    data.update(kw)
    with capture(execute=True):
        res = api.post(TICKETS_URL, data, format='multipart')
    assert res.status_code == 201, res.data
    return Ticket.objects.get(pk=res.data['id'])


def pending(**kw):
    return make_ticket(**kw, team_alert_pending=True, client_alert_pending=True)


def team_mail():
    return [m for m in mail.outbox if m.to == ['team@aps.example']]


@pytest.mark.parametrize('urgency', ['normal', 'urgent'])
def test_non_emergency_waits_for_the_job(api, site, urgency, django_capture_on_commit_callbacks):
    ticket = raise_ticket(api, site, django_capture_on_commit_callbacks, urgency=urgency)
    assert mail.outbox == []
    assert ticket.team_alert_pending and ticket.client_alert_pending

    alerts.send_new_ticket_alerts()
    assert len(team_mail()) == 1
    assert [m.subject for m in mail.outbox if m.to == ['sipho@acme.example']] == [f'We received your ticket {ticket.reference}']
    ticket.refresh_from_db()
    assert not ticket.team_alert_pending and not ticket.client_alert_pending


def test_emergency_is_emailed_at_once_and_not_again(api, site, django_capture_on_commit_callbacks):
    ticket = raise_ticket(api, site, django_capture_on_commit_callbacks, urgency='emergency')
    assert len(team_mail()) == 1 and team_mail()[0].subject.startswith('[Emergency]')
    assert any(m.to == ['sipho@acme.example'] for m in mail.outbox)
    mail.outbox.clear()
    assert alerts.send_new_ticket_alerts()['team_tickets'] == 0
    assert mail.outbox == []
    ticket.refresh_from_db()
    assert not ticket.team_alert_pending


def test_one_team_email_for_all_new_tickets(client_user, site, other_client, other_site):
    a = pending(client=client_user, site=site, title='First', urgency=Ticket.Urgency.NORMAL)
    b = pending(client=client_user, site=site, title='Second', urgency=Ticket.Urgency.URGENT)
    c = pending(client=other_client, site=other_site, title='Third')

    result = alerts.send_new_ticket_alerts()
    assert result == {'skipped': False, 'team_tickets': 3, 'clients_emailed': 3}
    team = team_mail()
    assert len(team) == 1
    assert team[0].subject == f'3 new tickets (1 urgent): {a.reference}, {b.reference}, {c.reference}'
    for t in (a, b, c):
        assert t.title in team[0].body
        assert f'https://frontend.example/dashboard/tickets/{t.pk}' in team[0].body
    # One confirmation per ticket, to the right client.
    assert sorted(m.to[0] for m in mail.outbox if m not in team) == [
        'ops@other.example', 'sipho@acme.example', 'sipho@acme.example']


def test_single_ticket_keeps_the_detailed_subject(client_user, site):
    t = pending(client=client_user, site=site, title='Geyser leak', urgency=Ticket.Urgency.URGENT)
    alerts.send_new_ticket_alerts()
    assert team_mail()[0].subject == f'[Urgent] New ticket {t.reference}: Geyser leak (Acme Properties)'


def test_nothing_sent_twice(client_user, site):
    pending(client=client_user, site=site)
    alerts.send_new_ticket_alerts()
    sent = len(mail.outbox)
    assert alerts.send_new_ticket_alerts() == {'skipped': False, 'team_tickets': 0, 'clients_emailed': 0}
    assert len(mail.outbox) == sent


def test_nothing_pending(db):
    assert alerts.send_new_ticket_alerts() == {'skipped': False, 'team_tickets': 0, 'clients_emailed': 0}
    assert mail.outbox == []


def test_ticket_the_team_already_moved_on_is_cleared_silently(client_user, site):
    t = pending(client=client_user, site=site, status=Ticket.Status.VISIT_BOOKED)
    alerts.send_new_ticket_alerts()
    assert mail.outbox == []
    t.refresh_from_db()
    assert not t.team_alert_pending and not t.client_alert_pending


def test_unconfirmed_client_gets_no_confirmation(site):
    user = make_client('plainco', email='maybe@plain.example', verified=False)
    their_site = Site.objects.create(client=user, name='Only site')
    t = pending(client=user, site=their_site)
    alerts.send_new_ticket_alerts()
    assert [m.to for m in mail.outbox] == [['team@aps.example']]
    t.refresh_from_db()
    assert not t.client_alert_pending


def test_team_smtp_failure_is_retried(client_user, site, monkeypatch):
    t = pending(client=client_user, site=site)
    monkeypatch.setattr(alerts, 'notify_team_new_tickets', lambda tickets: False)
    alerts.send_new_ticket_alerts()
    t.refresh_from_db()
    assert t.team_alert_pending  # retried next run
    assert not t.client_alert_pending  # the client's confirmation still went
    monkeypatch.undo()
    alerts.send_new_ticket_alerts()
    assert len(team_mail()) == 1


def test_client_smtp_failure_is_retried(client_user, site, monkeypatch):
    t = pending(client=client_user, site=site)
    monkeypatch.setattr(alerts, 'send_ticket_confirmation', lambda ticket: False)
    alerts.send_new_ticket_alerts()
    t.refresh_from_db()
    assert t.client_alert_pending and not t.team_alert_pending


def test_team_email_needs_recipient(settings, client_user, site):
    settings.EMAIL_RECIPIENT = ''
    t = pending(client=client_user, site=site)
    alerts.send_new_ticket_alerts()
    t.refresh_from_db()
    assert t.team_alert_pending


def test_does_not_reorder_the_inbox(client_user, site):
    t = pending(client=client_user, site=site)
    Ticket.objects.filter(pk=t.pk).update(updated_at=t.updated_at - timedelta(hours=3))
    before = Ticket.objects.get(pk=t.pk).updated_at
    alerts.send_new_ticket_alerts()
    assert Ticket.objects.get(pk=t.pk).updated_at == before


def test_overlapping_run_is_skipped(client_user, site):
    pending(client=client_user, site=site)
    cache.add(alerts.LOCK_KEY, 1, 60)
    assert alerts.send_new_ticket_alerts()['skipped'] is True
    assert mail.outbox == []
    cache.delete(alerts.LOCK_KEY)
    alerts.send_new_ticket_alerts()
    assert len(team_mail()) == 1


def test_user_text_is_escaped_in_the_roundup(client_user, site):
    pending(client=client_user, site=site, title='<script>x</script>')
    pending(client=client_user, site=site, title='Second')
    alerts.send_new_ticket_alerts()
    html = team_mail()[0].body
    assert '<script>' not in html and '&lt;script&gt;' in html


def test_management_command(client_user, site):
    pending(client=client_user, site=site)
    call_command('send_new_ticket_alerts')
    assert len(team_mail()) == 1


def test_celery_task(client_user, site):
    from apps.client_portal.tasks import send_new_ticket_alerts

    pending(client=client_user, site=site)
    assert send_new_ticket_alerts() == 'team told about 1 ticket(s); 1 client confirmation(s)'


def test_schedule_migration_targets_the_task_every_30_minutes():
    # Test settings don't install django_celery_beat, so check the migration itself.
    from apps.client_portal.tasks import send_new_ticket_alerts

    mig = importlib.import_module('apps.client_portal.migrations.0012_new_ticket_alert_schedule')
    assert mig.TASK_PATH == f'{send_new_ticket_alerts.__module__}.{send_new_ticket_alerts.__name__}'
    src = inspect.getsource(mig.create_schedule)
    assert "minute='0,30'" in src and "hour='*'" in src


def test_emergency_still_pings_telegram_at_once(api, site, settings, django_capture_on_commit_callbacks):
    settings.ALERT_TELEGRAM_ENABLED = True
    settings.TELEGRAM_BOT_TOKEN = 'bot-token'
    settings.TELEGRAM_CHAT_ID = '42'
    with patch('apps.client_portal.emails.requests.post') as post:
        raise_ticket(api, site, django_capture_on_commit_callbacks, urgency='emergency')
        raise_ticket(api, site, django_capture_on_commit_callbacks, urgency='normal')
        alerts.send_new_ticket_alerts()
    assert post.call_count == 1
