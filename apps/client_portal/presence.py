"""
Online / last-online for the ticket chat (``ChatPresence``).

- The live connection (consumers.py) counts tabs in and out and sends a
  heartbeat about every 25 s.
- A visible chat poll (``?read=1``) also counts as being around.

Online = an open connection with a heartbeat in the last 75 s. Clients see the
APS team as one ("online" while any staff member is), staff see the client.
"""

from datetime import timedelta

from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from apps.core.roles import is_dashboard_admin

from .models import ChatPresence

ONLINE_WINDOW = timedelta(seconds=75)


def side_of(user):
    return ChatPresence.Side.STAFF if is_dashboard_admin(user) else ChatPresence.Side.CLIENT


def touch(user, connections=0):
    """Mark ``user`` as around now; ``connections`` +1 / -1 on connect / disconnect."""
    now = timezone.now()
    side = side_of(user)
    obj, created = ChatPresence.objects.get_or_create(
        user=user, defaults={'side': side, 'last_seen': now, 'connections': max(0, connections)},
    )
    if created:
        return
    changes = {'last_seen': now, 'side': side}
    if connections > 0:
        changes['connections'] = F('connections') + 1
    elif connections < 0:
        changes['connections'] = Greatest(F('connections') - 1, Value(0))
    ChatPresence.objects.filter(pk=obj.pk).update(**changes)


def _state(rows):
    cutoff = timezone.now() - ONLINE_WINDOW
    online = any(r.connections > 0 and r.last_seen and r.last_seen >= cutoff for r in rows)
    last_seen = max((r.last_seen for r in rows if r.last_seen), default=None)
    return {'online': online, 'last_seen': last_seen}


def client_presence(user):
    return _state(list(ChatPresence.objects.filter(user=user)))


def team_presence():
    return _state(list(ChatPresence.objects.filter(side=ChatPresence.Side.STAFF)))
