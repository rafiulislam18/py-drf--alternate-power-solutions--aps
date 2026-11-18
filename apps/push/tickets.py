"""
Push for ticket chat messages (APS dashboard).

- A client's message goes to every staff member who can read ticket chats
  (``tickets.chat_view``; superusers always can).
- A staff message goes to the ticket's client.
- Never to the sender.

The task runs :data:`PUSH_DELAY_SECONDS` after the message. By then, anyone
with that chat open on screen has fetched it with ``read=1`` (the live
connection pokes their page straight away), which moves their own
``TicketChatRead`` marker past the message, so they're skipped. If more
messages from the same side came in meanwhile, only the newest one sends, so a
burst becomes one notification. One notification per ticket: the tag replaces
the previous one instead of stacking.

The unread-message emails (digests.py) are separate and unchanged.
"""

import logging

from apps.client_portal.models import TicketChatRead, TicketMessage
from apps.client_portal.serializers import TicketMessageSerializer
from apps.staff.access import has_perm

from .models import PushSubscription
from .queue import after_commit
from .send import deliver

logger = logging.getLogger('apps.push')

PUSH_DELAY_SECONDS = 5
PREVIEW_CHARS = 120


def queue_ticket_message_push(message):
    from .tasks import push_ticket_message
    after_commit(push_ticket_message, [message.pk], countdown=PUSH_DELAY_SECONDS)


def ticket_tag(ticket):
    # Same tag as the in-tab desktop notification (TicketChat.tsx), so the two merge.
    return f'aps-ticket-{ticket.pk}'


def preview(text):
    text = ' '.join(text.split())
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS - 1].rstrip() + '…'


def _subscriptions_for(message):
    ticket = message.ticket
    subs = PushSubscription.objects.filter(app=PushSubscription.App.APS, user__is_active=True)
    if message.author_id:
        subs = subs.exclude(user_id=message.author_id)
    if message.author_role == TicketMessage.Author.STAFF:
        return list(subs.filter(user_id=ticket.client_id))
    subs = subs.select_related('user__client_profile', 'user__staff_access')
    return [s for s in subs if has_perm(s.user, 'tickets.chat_view')]


def send_ticket_message_push(message_id):
    message = (
        TicketMessage.objects.select_related('ticket__client__client_profile', 'author')
        .filter(pk=message_id).first()
    )
    if message is None:
        return None  # deleted with its ticket
    ticket = message.ticket
    if TicketMessage.objects.filter(ticket=ticket, author_role=message.author_role, pk__gt=message.pk).exists():
        return None  # a newer message from the same side sends instead

    seen = set(
        TicketChatRead.objects.filter(ticket=ticket, read_upto__gte=message.pk).values_list('user_id', flat=True)
    )
    subs = [s for s in _subscriptions_for(message) if s.user_id not in seen]
    if not subs:
        return None

    author = TicketMessageSerializer().get_author_name(message)
    payload = {
        'app': 'aps',
        'title': f'New message on {ticket.reference}',
        'body': f'{author}: {preview(message.body)}',
        'tag': ticket_tag(ticket),
        'url': f'/dashboard/tickets/{ticket.pk}',
    }
    counts = deliver(subs, payload)
    logger.info(f'Ticket {ticket.reference} message {message.pk} push: {counts}')
    return counts
