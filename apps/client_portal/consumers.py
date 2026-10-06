"""
The dashboard's live connection: ``wss://<api>/ws/dashboard/``.

1. The page connects and sends ``{"type": "auth", "token": "<access JWT>"}``
   (never in the URL, so it doesn't land in server logs). The same rules as
   the API apply: valid, active account, password unchanged since sign-in.
2. It then receives pokes — ``{"type": "ticket", "ticket_id", "kind"}`` for its
   own tickets (clients) or every ticket (staff), and ``{"type": "presence"}``
   when the other side comes or goes — and fetches the details over the API.
3. It sends ``{"type": "ping"}`` about every 25 s; that's the online heartbeat.

No auth within 10 s, or a bad token, closes the socket with code 4401.
"""

import asyncio

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.authentication import _pv_matches

from . import presence
from .models import ChatPresence
from .realtime import CLIENTS_GROUP, STAFF_GROUP, user_group

AUTH_TIMEOUT = 10
CLOSE_UNAUTHORISED = 4401


def _user_for_token(token):
    if not isinstance(token, str) or not token:
        return None
    try:
        access = AccessToken(token)
    except Exception:  # noqa: BLE001 — expired, tampered, wrong type
        return None
    user = (
        get_user_model().objects.select_related('client_profile')
        .filter(**{api_settings.USER_ID_FIELD: access.get(api_settings.USER_ID_CLAIM)}).first()
    )
    if user is None or not user.is_active or not _pv_matches(user, access):
        return None
    return user


class DashboardConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = None
        self.side = None
        self.joined = []
        await self.accept()
        self.auth_timer = asyncio.get_running_loop().call_later(
            AUTH_TIMEOUT, lambda: asyncio.ensure_future(self._close_if_anonymous()),
        )

    async def _close_if_anonymous(self):
        if self.user is None:
            await self.close(code=CLOSE_UNAUTHORISED)

    async def receive_json(self, content, **kwargs):
        kind = content.get('type') if isinstance(content, dict) else None
        if self.user is None:
            if kind != 'auth':
                return await self.close(code=CLOSE_UNAUTHORISED)
            user = await database_sync_to_async(_user_for_token)(content.get('token'))
            if user is None:
                await self.send_json({'type': 'auth_failed'})
                return await self.close(code=CLOSE_UNAUTHORISED)
            self.user = user
            self.side = await database_sync_to_async(presence.side_of)(user)
            staff = self.side == ChatPresence.Side.STAFF
            self.joined = [user_group(user.pk), STAFF_GROUP if staff else CLIENTS_GROUP]
            for group in self.joined:
                await self.channel_layer.group_add(group, self.channel_name)
            await database_sync_to_async(presence.touch)(user, connections=1)
            await self._announce()
            await self.send_json({'type': 'ready', 'side': self.side})
        elif kind == 'ping':
            await database_sync_to_async(presence.touch)(self.user)
            await self.send_json({'type': 'pong'})

    async def disconnect(self, code):
        timer = getattr(self, 'auth_timer', None)
        if timer:
            timer.cancel()
        for group in getattr(self, 'joined', []):
            await self.channel_layer.group_discard(group, self.channel_name)
        if getattr(self, 'user', None) is not None:
            await database_sync_to_async(presence.touch)(self.user, connections=-1)
            await self._announce()

    async def _announce(self):
        """Tell the other side this person came or went (they refetch presence)."""
        target = STAFF_GROUP if self.side == ChatPresence.Side.CLIENT else CLIENTS_GROUP
        await self.channel_layer.group_send(
            target, {'type': 'poke', 'data': {'type': 'presence', 'side': self.side, 'user_id': self.user.pk}},
        )

    async def poke(self, event):
        await self.send_json(event['data'])
