"""
Live "something changed" pokes for the dashboard (see consumers.py).

A poke only says what changed — e.g. ``{"type": "ticket", "ticket_id": 7,
"kind": "message"}`` — and the page then fetches it through the normal API, so
every permission check stays in one place. Pokes go out after the database
commit. If the channel layer (Redis) is down they're dropped with a warning:
the pages fall back to polling, so nothing is lost.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger('apps.client_portal')

STAFF_GROUP = 'dashboard.staff'
CLIENTS_GROUP = 'dashboard.clients'


def user_group(user_id):
    return f'dashboard.user.{user_id}'


def send(group, data):
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group, {'type': 'poke', 'data': data})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f'Live update to {group} not sent: {exc}')


def ticket_changed(ticket, kind):
    """Tell the ticket's client and all staff that ``ticket`` changed (after commit)."""
    data = {'type': 'ticket', 'ticket_id': ticket.pk, 'kind': kind}
    client_id = ticket.client_id

    def go():
        send(user_group(client_id), data)
        send(STAFF_GROUP, data)

    transaction.on_commit(go)
