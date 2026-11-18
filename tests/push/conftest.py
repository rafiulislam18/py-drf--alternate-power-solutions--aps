"""
Shared fixtures for push notifications (apps.push).

Nothing is ever sent: ``webpush`` is patched (``sent`` fixture) and the VAPID
keys are dummies (``vapid`` fixture turns push "on").
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from pywebpush import WebPushException

from apps.core.models import ClientProfile
from apps.gas_guard.users.models import GasGuardUser
from apps.push.models import PushSubscription
from apps.staff.models import StaffAccess

P256DH = 'B' + 'A' * 86
AUTH = 'A' * 22
WEBPUSH = 'apps.push.send.webpush'
CHROME_ANDROID = ('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/131.0.0.0 Mobile Safari/537.36')


def endpoint(n):
    return f'https://fcm.googleapis.com/fcm/send/device-{n}'


def body(n=1):
    return {'endpoint': endpoint(n), 'keys': {'p256dh': P256DH, 'auth': AUTH}, 'expirationTime': None}


def make_staff(username, perms, **kw):
    user = User.objects.create_user(username, password='x', **kw)
    ClientProfile.objects.create(user=user, role='admin')
    StaffAccess.objects.create(user=user, permissions=perms)
    return user


def make_gg_user(email, **kw):
    user = GasGuardUser(email=email, **kw)
    user.set_password('x')
    user.save()
    return user


def aps_device(user, n, label='Chrome on Android'):
    return PushSubscription.objects.create(app='aps', user=user, endpoint=endpoint(n), p256dh=P256DH, auth=AUTH,
                                           device_label=label)


def gg_device(user, n, label='Chrome on Android'):
    return PushSubscription.objects.create(app='gas_guard', gg_user=user, endpoint=endpoint(n), p256dh=P256DH,
                                           auth=AUTH, device_label=label)


def push_error(status):
    return WebPushException('Push failed', response=MagicMock(status_code=status))


def payloads(mock):
    """[(endpoint, payload dict)] for every webpush call."""
    import json
    return [(c.kwargs['subscription_info']['endpoint'], json.loads(c.kwargs['data'])) for c in mock.call_args_list]


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def vapid(settings):
    settings.VAPID_PUBLIC_KEY = 'BPublicKeyForTests'
    settings.VAPID_PRIVATE_KEY = 'private-key-for-tests'
    settings.VAPID_SUBJECT = 'mailto:support@example.com'
    return settings


@pytest.fixture
def sent(vapid):
    with patch(WEBPUSH) as mock:
        yield mock
