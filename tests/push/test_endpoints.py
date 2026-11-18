"""
Tests for the push endpoints of both apps: config, turning a device on/off,
the device list, removing a device and the test notification. APS uses the
dashboard login (staff and clients), Gas Guard its own users and tokens.
"""

import pytest
from django.contrib.auth.models import User

from apps.push.models import PushSubscription
from apps.push.throttling import PushTestThrottle
from apps.push.views import MAX_DEVICES
from apps.records.models import AuditLog
from tests.client_portal.conftest import api_for, bearer, login, make_client
from tests.gas_guard.conftest import auth_client_for

from .conftest import (CHROME_ANDROID, aps_device, body, endpoint, gg_device, make_gg_user, payloads, push_error)

APS = '/push/aps/'
GG = '/gas-guard/push/'


@pytest.fixture
def client_user(db):
    return make_client('acme', email='sipho@acme.example', company_name='Acme Properties')


@pytest.fixture
def staff_user(db):
    return User.objects.create_user('thabo', password='x', is_staff=True, first_name='Thabo')


@pytest.fixture
def gg_owner(db):
    return make_gg_user('owner@example.com')


def subscribe(api, base, n=1, ua=CHROME_ANDROID):
    return api.post(f'{base}subscriptions/', body(n), format='json', HTTP_USER_AGENT=ua)


# ── config ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize('base', [APS, GG])
def test_config_is_public_and_off_without_keys(base):
    res = api_for().get(f'{base}config/')
    assert res.status_code == 200
    assert res.json() == {'enabled': False, 'public_key': ''}


@pytest.mark.django_db
@pytest.mark.parametrize('base', [APS, GG])
def test_config_gives_public_key_when_set_up(base, vapid):
    res = api_for().get(f'{base}config/', HTTP_AUTHORIZATION='Bearer expired-or-junk')
    assert res.json() == {'enabled': True, 'public_key': 'BPublicKeyForTests'}


# ── who may use which app's endpoints ───────────────────────────────────────


@pytest.mark.django_db
def test_sign_in_required(vapid):
    for base in (APS, GG):
        assert subscribe(api_for(), base).status_code == 401
        assert api_for().get(f'{base}subscriptions/').status_code == 401
        assert api_for().post(f'{base}test/').status_code == 401
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
def test_tokens_dont_cross_between_apps(vapid, client_user, gg_owner):
    # A Gas Guard token on the APS endpoints, and an APS (dashboard) user on Gas Guard's.
    assert subscribe(auth_client_for(gg_owner), APS).status_code == 401
    access = login('acme').json()['access']
    assert subscribe(bearer(access), GG).status_code == 401
    assert subscribe(bearer(access), APS).status_code == 201


# ── turning a device on ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_aps_client_and_staff_can_subscribe(vapid, client_user, staff_user):
    res = subscribe(api_for(client_user), APS, 1)
    assert res.status_code == 201
    assert res.json()['device_label'] == 'Chrome on Android'
    assert subscribe(api_for(staff_user), APS, 2).status_code == 201
    sub = PushSubscription.objects.get(endpoint=endpoint(1))
    assert (sub.app, sub.user, sub.gg_user) == ('aps', client_user, None)
    assert (sub.p256dh, sub.auth) == (body()['keys']['p256dh'], body()['keys']['auth'])


@pytest.mark.django_db
def test_gas_guard_subscribe_uses_gas_guard_user(vapid, gg_owner):
    res = subscribe(auth_client_for(gg_owner), GG)
    assert res.status_code == 201
    sub = PushSubscription.objects.get()
    assert (sub.app, sub.user, sub.gg_user) == ('gas_guard', None, gg_owner)


@pytest.mark.django_db
def test_subscribe_refused_when_server_not_set_up(client_user):
    res = subscribe(api_for(client_user), APS)
    assert res.status_code == 503
    assert 'set up' in res.json()['detail']
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
def test_same_device_again_updates_instead_of_duplicating(vapid, client_user):
    api = api_for(client_user)
    assert subscribe(api, APS).status_code == 201
    again = body()
    again['keys']['auth'] = 'B' * 22
    res = api.post(f'{APS}subscriptions/', again, format='json')
    assert res.status_code == 200
    assert PushSubscription.objects.get().auth == 'B' * 22


@pytest.mark.django_db
def test_shared_device_moves_to_whoever_signs_in(vapid, client_user, staff_user):
    subscribe(api_for(client_user), APS)
    assert subscribe(api_for(staff_user), APS).status_code == 201
    assert PushSubscription.objects.get().user == staff_user


@pytest.mark.django_db
@pytest.mark.parametrize('url', [
    'http://fcm.googleapis.com/fcm/send/x',      # not https
    'https://localhost/push',                    # our own network
    'https://10.0.0.5/push',
    'https://evil.example/fcm.googleapis.com',   # host only looks right
    'https://fcm.googleapis.com.evil.example/x',
    'not a url',
])
def test_only_real_push_services_accepted(vapid, client_user, url):
    res = api_for(client_user).post(f'{APS}subscriptions/', {**body(), 'endpoint': url}, format='json')
    assert res.status_code == 400
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('url', [
    'https://fcm.googleapis.com/fcm/send/abc',
    'https://web.push.apple.com/QGx9',
    'https://updates.push.services.mozilla.com/wpush/v2/abc',
    'https://wns2-db5p.notify.windows.com/w/?token=abc',
])
def test_known_push_services_accepted(vapid, client_user, url):
    res = api_for(client_user).post(f'{APS}subscriptions/', {**body(), 'endpoint': url}, format='json')
    assert res.status_code == 201


@pytest.mark.django_db
@pytest.mark.parametrize('keys', [{}, {'p256dh': 'short', 'auth': 'A' * 22}, {'p256dh': '!' * 87, 'auth': 'A' * 22}])
def test_bad_keys_refused(vapid, client_user, keys):
    res = api_for(client_user).post(f'{APS}subscriptions/', {**body(), 'keys': keys}, format='json')
    assert res.status_code == 400


@pytest.mark.django_db
def test_oldest_devices_dropped_past_the_limit(vapid, client_user):
    for n in range(MAX_DEVICES):
        aps_device(client_user, n)
    first = PushSubscription.objects.order_by('created_at').first()
    assert subscribe(api_for(client_user), APS, 999).status_code == 201
    assert PushSubscription.objects.filter(user=client_user).count() == MAX_DEVICES
    assert not PushSubscription.objects.filter(pk=first.pk).exists()


# ── list, remove, unsubscribe ───────────────────────────────────────────────


@pytest.mark.django_db
def test_list_shows_only_my_devices_of_this_app(client_user, staff_user, gg_owner):
    mine = aps_device(client_user, 1)
    aps_device(staff_user, 2)
    gg_device(gg_owner, 3)
    res = api_for(client_user).get(f'{APS}subscriptions/')
    assert [d['id'] for d in res.json()] == [mine.pk]
    assert res.json()[0]['endpoint_hash'] == mine.endpoint_hash
    assert 'endpoint' not in res.json()[0]
    assert [d['id'] for d in auth_client_for(gg_owner).get(f'{GG}subscriptions/').json()] == [
        PushSubscription.objects.get(app='gas_guard').pk]


@pytest.mark.django_db
def test_remove_own_device_only(client_user, staff_user):
    mine, theirs = aps_device(client_user, 1), aps_device(staff_user, 2)
    api = api_for(client_user)
    assert api.delete(f'{APS}subscriptions/{theirs.pk}/').status_code == 404
    assert api.delete(f'{APS}subscriptions/{mine.pk}/').status_code == 204
    assert list(PushSubscription.objects.all()) == [theirs]


@pytest.mark.django_db
def test_gas_guard_cannot_remove_aps_device(client_user, gg_owner):
    aps = aps_device(client_user, 1)
    assert auth_client_for(gg_owner).delete(f'{GG}subscriptions/{aps.pk}/').status_code == 404
    assert PushSubscription.objects.filter(pk=aps.pk).exists()


@pytest.mark.django_db
def test_unsubscribe_this_device_by_endpoint(client_user, staff_user):
    aps_device(client_user, 1)
    aps_device(staff_user, 2)
    api = api_for(client_user)
    # Someone else's endpoint: nothing happens (and nothing is revealed).
    assert api.post(f'{APS}unsubscribe/', {'endpoint': endpoint(2)}, format='json').status_code == 204
    assert PushSubscription.objects.count() == 2
    assert api.post(f'{APS}unsubscribe/', {'endpoint': endpoint(1)}, format='json').status_code == 204
    assert api.post(f'{APS}unsubscribe/', {'endpoint': endpoint(1)}, format='json').status_code == 204
    assert list(PushSubscription.objects.values_list('user', flat=True)) == [staff_user.pk]


# ── audit history (APS only) ────────────────────────────────────────────────


@pytest.mark.django_db
def test_aps_on_and_off_are_in_the_audit_history(vapid, client_user):
    api = api_for(client_user)
    subscribe(api, APS)
    subscribe(api, APS)  # same device again: not logged twice
    sub = PushSubscription.objects.get()
    api.delete(f'{APS}subscriptions/{sub.pk}/')
    rows = list(AuditLog.objects.order_by('id').values_list('action', 'summary', 'object_type'))
    assert rows == [('notifications_on', 'Chrome on Android', 'auth.user'),
                    ('notifications_off', 'Chrome on Android', 'auth.user')]


@pytest.mark.django_db
def test_gas_guard_changes_not_in_aps_audit(vapid, gg_owner):
    subscribe(auth_client_for(gg_owner), GG)
    assert not AuditLog.objects.exists()


# ── test notification ───────────────────────────────────────────────────────


@pytest.mark.django_db
def test_test_notification_sent_straight_away(sent, client_user):
    aps_device(client_user, 1)
    aps_device(client_user, 2)
    res = api_for(client_user).post(f'{APS}test/', {'endpoint': endpoint(2)}, format='json')
    assert res.status_code == 200
    assert res.json() == {'detail': 'Test notification sent.', 'sent': 1, 'gone': 0, 'failed': 0}
    [(url, payload)] = payloads(sent)
    assert url == endpoint(2)
    assert payload == {'app': 'aps', 'title': 'Test notification', 'body': 'Notifications are working on this device.',
                       'tag': 'aps-test', 'url': '/dashboard/account'}


@pytest.mark.django_db
def test_test_notification_without_endpoint_goes_to_all_my_devices(sent, gg_owner):
    gg_device(gg_owner, 1)
    gg_device(gg_owner, 2)
    res = auth_client_for(gg_owner).post(f'{GG}test/', {}, format='json')
    assert res.json()['sent'] == 2
    assert {p['app'] for _, p in payloads(sent)} == {'gas_guard'}
    assert payloads(sent)[0][1]['url'] == '/gas-guard/dashboard/settings'


@pytest.mark.django_db
def test_test_notification_expired_device_removed(sent, client_user):
    aps_device(client_user, 1)
    sent.side_effect = push_error(410)
    res = api_for(client_user).post(f'{APS}test/', {'endpoint': endpoint(1)}, format='json')
    assert res.json()['gone'] == 1
    assert 'expired' in res.json()['detail']
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
def test_test_notification_needs_a_device(sent, client_user, staff_user):
    aps_device(staff_user, 1)
    api = api_for(client_user)
    assert api.post(f'{APS}test/', {}, format='json').status_code == 400
    # Another person's endpoint counts as "not this device's".
    assert api.post(f'{APS}test/', {'endpoint': endpoint(1)}, format='json').status_code == 400
    sent.assert_not_called()


@pytest.mark.django_db
def test_test_notification_refused_when_not_set_up(client_user):
    aps_device(client_user, 1)
    assert api_for(client_user).post(f'{APS}test/', {}, format='json').status_code == 503


@pytest.mark.django_db
def test_test_notification_is_throttled(sent, client_user, monkeypatch):
    monkeypatch.setattr(PushTestThrottle, 'rate', '2/h', raising=False)
    aps_device(client_user, 1)
    api = api_for(client_user)
    codes = [api.post(f'{APS}test/', {}, format='json').status_code for _ in range(3)]
    assert codes == [200, 200, 429]
