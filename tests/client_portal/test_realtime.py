"""
Tests for the live ticket chat (apps.client_portal.consumers / realtime /
presence): socket sign-in, pokes on messages / reads / status changes scoped to
the right people, online / last-online, and the chat payload's presence.

Each test runs one async scenario (sockets live in one event loop); API calls
and ORM work go through ``S`` (sync_to_async).
"""

from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import User
from django.utils import timezone

from apps.client_portal import presence
from apps.client_portal.models import ChatPresence
from config.asgi import application

from .conftest import api_for, login, make_ticket

pytestmark = pytest.mark.django_db(transaction=True)

S = sync_to_async
ORIGIN = [(b'origin', b'http://localhost:5173')]


@pytest.fixture
def staff_user(db):
    return User.objects.create_user('ops', password='Sunny-Days-2026', is_staff=True, first_name='Thabo')


@pytest.fixture
def ticket(client_user, site):
    return make_ticket(client_user, site)


def token_for(username):
    res = login(username)
    assert res.status_code == 200, res.data
    return res.data['access']


async def ws(username=None, token=None):
    token = token or await S(token_for)(username)
    comm = WebsocketCommunicator(application, '/ws/dashboard/', headers=ORIGIN)
    connected, _ = await comm.connect()
    assert connected
    await comm.send_json_to({'type': 'auth', 'token': token})
    return comm, await comm.receive_json_from(timeout=3)


async def rx(comm):
    return await comm.receive_json_from(timeout=3)


def run(scenario):
    async_to_sync(scenario)()


def post(user, url, data):
    return api_for(user).post(url, data, format='json')


def test_auth_and_presence(client_user):
    async def scenario():
        comm, hello = await ws('acme')
        assert hello == {'type': 'ready', 'side': 'client'}
        assert (await S(presence.client_presence)(client_user))['online'] is True
        await comm.disconnect()
        state = await S(presence.client_presence)(client_user)
        assert state['online'] is False and state['last_seen'] is not None
    run(scenario)


def test_bad_token_is_refused(db):
    async def scenario():
        comm, reply = await ws(token='not-a-token')
        assert reply == {'type': 'auth_failed'}
        assert (await comm.receive_output(timeout=2))['code'] == 4401
    run(scenario)


def test_token_from_before_a_password_change_is_refused(client_user):
    token = token_for('acme')
    client_user.set_password('Another-Pass-2026')
    client_user.save()

    async def scenario():
        _, reply = await ws(token=token)
        assert reply == {'type': 'auth_failed'}
    run(scenario)


def test_message_pokes_the_client_and_staff_but_not_other_clients(client_user, staff_user, other_client, ticket):
    async def scenario():
        mine, _ = await ws('acme')
        staff, _ = await ws('ops')
        assert (await rx(mine))['type'] == 'presence'  # staff came online
        other, _ = await ws('otherco')
        assert (await rx(staff))['type'] == 'presence'  # another client came online

        res = await S(post)(staff_user, f'/client-portal/staff/tickets/{ticket.pk}/messages/', {'body': 'On our way'})
        assert res.status_code == 201
        poke = {'type': 'ticket', 'ticket_id': ticket.pk, 'kind': 'message'}
        assert await rx(mine) == poke
        assert await rx(staff) == poke
        assert await other.receive_nothing(timeout=0.3)  # another client's ticket: never poked
        for c in (mine, staff, other):
            await c.disconnect()
    run(scenario)


def test_read_and_status_pokes(client_user, staff_user, ticket):
    post(staff_user, f'/client-portal/staff/tickets/{ticket.pk}/messages/', {'body': 'Hi'})

    async def scenario():
        staff, _ = await ws('ops')
        await S(api_for(client_user).get)(f'/client-portal/tickets/{ticket.pk}/messages/?read=1')
        assert await rx(staff) == {'type': 'ticket', 'ticket_id': ticket.pk, 'kind': 'read'}
        mine, _ = await ws('acme')
        await rx(staff)  # presence
        await S(api_for(staff_user).patch)(f'/client-portal/staff/tickets/{ticket.pk}/', {'status': 'visit_booked'}, format='json')
        assert await rx(mine) == {'type': 'ticket', 'ticket_id': ticket.pk, 'kind': 'status'}
        await staff.disconnect()
        await mine.disconnect()
    run(scenario)


def test_presence_is_announced_to_the_other_side(client_user, staff_user):
    async def scenario():
        mine, _ = await ws('acme')
        staff, _ = await ws('ops')
        assert await rx(mine) == {'type': 'presence', 'side': 'staff', 'user_id': staff_user.pk}
        await staff.disconnect()
        assert await rx(mine) == {'type': 'presence', 'side': 'staff', 'user_id': staff_user.pk}
        await mine.disconnect()
    run(scenario)


def test_ping_is_the_heartbeat(client_user):
    async def scenario():
        comm, _ = await ws('acme')
        await S(ChatPresence.objects.filter(user=client_user).update)(last_seen=timezone.now() - timedelta(minutes=5))
        assert (await S(presence.client_presence)(client_user))['online'] is False  # no heartbeat for 5 min
        await comm.send_json_to({'type': 'ping'})
        assert await rx(comm) == {'type': 'pong'}
        assert (await S(presence.client_presence)(client_user))['online'] is True
        await comm.disconnect()
    run(scenario)


def test_chat_payload_carries_the_other_sides_presence(client_user, staff_user, ticket):
    async def scenario():
        staff, _ = await ws('ops')
        res = await S(api_for(client_user).get)(f'/client-portal/tickets/{ticket.pk}/messages/')
        assert res.data['presence']['online'] is True  # the APS team
        await staff.disconnect()
    run(scenario)
    res = api_for(staff_user).get(f'/client-portal/staff/tickets/{ticket.pk}/messages/')
    assert res.data['presence'] == {'online': False, 'last_seen': None}  # client never connected
    api_for(client_user).get(f'/client-portal/tickets/{ticket.pk}/messages/?read=1')  # looking at it
    res = api_for(staff_user).get(f'/client-portal/staff/tickets/{ticket.pk}/messages/')
    assert res.data['presence']['last_seen'] is not None and res.data['presence']['online'] is False
