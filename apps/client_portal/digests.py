"""
Unread ticket-chat emails, sent by a job every 30 minutes (Celery Beat — see
migration 0009 and ``tasks.send_ticket_chat_digests``).

Each run finds chat messages the other side hasn't read yet and hasn't already
been emailed about, and sends one email per recipient:

- each client (confirmed email only) gets one email covering all their tickets
  with unread APS replies;
- the APS team (EMAIL_RECIPIENT) gets one email covering every ticket with
  unread client messages.

Messages read in the dashboard are never emailed, a message is never emailed
twice (``Ticket.client_emailed_upto`` / ``staff_emailed_upto``), and messages
younger than :data:`MIN_AGE` wait for the next run — someone may be reading
them live right now. A cache lock stops two overlapping runs double-sending.
"""

import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from .emails import send_client_chat_digest, send_team_chat_digest
from .models import Ticket, TicketMessage

logger = logging.getLogger('apps.client_portal')

MIN_AGE = timedelta(minutes=2)
LOCK_KEY = 'ticket-chat-digest-lock'
LOCK_SECONDS = 25 * 60


def _unread(author_role, read_field, emailed_field, cutoff):
    """Messages by ``author_role`` the other side hasn't read or been emailed about."""
    return (
        TicketMessage.objects.filter(author_role=author_role, created_at__lte=cutoff)
        .filter(id__gt=F(f'ticket__{read_field}'))
        .filter(id__gt=F(f'ticket__{emailed_field}'))
        .select_related('ticket__client__client_profile', 'ticket__site', 'author')
        .order_by('ticket_id', 'id')
    )


def _group_by_ticket(messages):
    by_ticket = defaultdict(list)
    for m in messages:
        by_ticket[m.ticket].append(m)
    return by_ticket


def _mark_emailed(field, by_ticket):
    for ticket, msgs in by_ticket.items():
        newest = msgs[-1].pk
        # Only ever moves forward.
        Ticket.objects.filter(pk=ticket.pk, **{f'{field}__lt': newest}).update(**{field: newest})


def send_chat_digests(now=None):
    """Send the unread-message emails. Returns counts for logging/tests."""
    if not cache.add(LOCK_KEY, 1, LOCK_SECONDS):
        logger.info('Ticket chat digest skipped: another run is in progress.')
        return {'skipped': True, 'clients_emailed': 0, 'team_emailed': False}
    try:
        cutoff = (now or timezone.now()) - MIN_AGE
        result = {'skipped': False, 'clients_emailed': 0, 'team_emailed': False}

        # APS replies the client hasn't seen → one email per client.
        to_clients = _group_by_ticket(_unread('staff', 'client_read_upto', 'client_emailed_upto', cutoff))
        per_client = defaultdict(dict)
        for ticket, msgs in to_clients.items():
            per_client[ticket.client][ticket] = msgs
        for client, tickets in per_client.items():
            profile = getattr(client, 'client_profile', None)
            if client.email and profile and profile.email_verified:
                if send_client_chat_digest(client, tickets):
                    result['clients_emailed'] += 1
                else:
                    continue  # SMTP failed: leave unmarked so the next run retries
            # No confirmed email: nothing to send to, and nothing to send later
            # either (stale news) — mark handled.
            _mark_emailed('client_emailed_upto', tickets)

        # Client messages the team hasn't seen → one email to the team.
        to_team = _group_by_ticket(_unread('client', 'staff_read_upto', 'staff_emailed_upto', cutoff))
        if to_team:
            if not settings.EMAIL_RECIPIENT:
                logger.warning('Ticket chat digest: EMAIL_RECIPIENT is not set; team email skipped.')
            elif send_team_chat_digest(to_team):
                result['team_emailed'] = True
                _mark_emailed('staff_emailed_upto', to_team)

        if result['clients_emailed'] or result['team_emailed']:
            logger.info(f"Ticket chat digest: {result['clients_emailed']} client email(s), "
                        f"team email {'sent' if result['team_emailed'] else 'not needed'}.")
        return result
    finally:
        cache.delete(LOCK_KEY)
