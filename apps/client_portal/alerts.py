"""
New-ticket emails, sent by a job every 30 minutes (Celery Beat — see migration
0012 and ``tasks.send_new_ticket_alerts``).

Raising a normal or urgent ticket only flags it (``team_alert_pending`` /
``client_alert_pending``); each run then sends:

- the APS team (EMAIL_RECIPIENT) one email covering every ticket raised since
  the last run;
- each client the "we received your ticket" confirmation (confirmed email only).

Emergency tickets skip the wait: they are emailed (and pinged to Telegram) as
soon as they're saved — see ``views._notify_new_ticket``.

A ticket the team has already moved on from Open (they've seen it, and the
client got a status-update email) is just cleared. If sending fails the flag
stays set, so the next run retries. A cache lock stops two overlapping runs
double-sending.
"""

import logging

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q

from .emails import client_can_be_emailed, notify_team_new_tickets, send_ticket_confirmation
from .models import Ticket

logger = logging.getLogger('apps.client_portal')

LOCK_KEY = 'ticket-new-alert-lock'
LOCK_SECONDS = 25 * 60


def _clear(field, tickets):
    # .update() leaves updated_at alone, so the inbox order doesn't change.
    Ticket.objects.filter(pk__in=[t.pk for t in tickets]).update(**{field: False})


def send_new_ticket_alerts():
    """Send the pending new-ticket emails. Returns counts for logging/tests."""
    if not cache.add(LOCK_KEY, 1, LOCK_SECONDS):
        logger.info('New-ticket emails skipped: another run is in progress.')
        return {'skipped': True, 'team_tickets': 0, 'clients_emailed': 0}
    try:
        result = {'skipped': False, 'team_tickets': 0, 'clients_emailed': 0}
        pending = list(
            Ticket.objects.filter(Q(team_alert_pending=True) | Q(client_alert_pending=True))
            .select_related('client__client_profile', 'site')
            .order_by('id')
        )

        team = [t for t in pending if t.team_alert_pending]
        still_open = [t for t in team if t.status == Ticket.Status.OPEN]
        if still_open and not settings.EMAIL_RECIPIENT:
            logger.warning('New-ticket emails: EMAIL_RECIPIENT is not set; team email skipped.')
        elif not still_open or notify_team_new_tickets(still_open):
            result['team_tickets'] = len(still_open)
            _clear('team_alert_pending', team)

        for ticket in (t for t in pending if t.client_alert_pending):
            if ticket.status == Ticket.Status.OPEN and client_can_be_emailed(ticket):
                if not send_ticket_confirmation(ticket):
                    continue  # SMTP failed: leave it flagged so the next run retries
                result['clients_emailed'] += 1
            # Sent, or nothing to send (no confirmed email / already moved on).
            _clear('client_alert_pending', [ticket])

        if result['team_tickets'] or result['clients_emailed']:
            logger.info(f"New-ticket emails: team told about {result['team_tickets']} ticket(s), "
                        f"{result['clients_emailed']} client confirmation(s).")
        return result
    finally:
        cache.delete(LOCK_KEY)
