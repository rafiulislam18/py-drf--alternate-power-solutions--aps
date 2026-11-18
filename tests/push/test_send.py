"""
Tests for the sender (apps.push.send), the device names, the key generator and
the model's owner rule. ``webpush`` is always mocked.
"""

import base64
from unittest.mock import patch

import pytest
import requests
from django.core.management import call_command
from django.db import IntegrityError, transaction
from py_vapid import Vapid

from apps.push.devices import device_label
from apps.push.models import PushSubscription
from apps.push.send import deliver, send_one
from tests.client_portal.conftest import make_client

from .conftest import AUTH, P256DH, WEBPUSH, aps_device, endpoint, make_gg_user, push_error

PAYLOAD = {'app': 'aps', 'title': 'T', 'body': 'B', 'tag': 'aps-ticket-7', 'url': '/dashboard/tickets/7'}


@pytest.fixture
def device(db):
    return aps_device(make_client('acme'), 1)


@pytest.mark.django_db
def test_nothing_sent_without_keys(device):
    with patch(WEBPUSH) as mock:
        assert deliver([device], PAYLOAD) == {'sent': 0, 'gone': 0, 'failed': 0}
    mock.assert_not_called()


@pytest.mark.django_db
def test_sends_encrypted_payload_with_vapid_and_headers(sent, device):
    assert send_one(device, PAYLOAD) == 'sent'
    kw = sent.call_args.kwargs
    assert kw['subscription_info'] == {'endpoint': endpoint(1), 'keys': {'p256dh': P256DH, 'auth': AUTH}}
    assert kw['vapid_private_key'] == 'private-key-for-tests'
    assert kw['vapid_claims'] == {'sub': 'mailto:support@example.com'}
    assert kw['headers'] == {'Urgency': 'high', 'Topic': 'aps-ticket-7'}
    assert kw['ttl'] > 0 and kw['timeout']
    device.refresh_from_db()
    assert device.last_used_at is not None


@pytest.mark.django_db
def test_each_send_gets_its_own_claims_dict(sent, device):
    # pywebpush writes the endpoint's "aud" into the dict it's given.
    send_one(device, PAYLOAD)
    send_one(device, PAYLOAD)
    first, second = (c.kwargs['vapid_claims'] for c in sent.call_args_list)
    assert first is not second


@pytest.mark.django_db
def test_topic_is_made_safe_and_short(sent, device):
    send_one(device, {**PAYLOAD, 'tag': 'gg site #12 with a rather long name indeed'})
    topic = sent.call_args.kwargs['headers']['Topic']
    assert len(topic) <= 32 and topic.replace('-', '').replace('_', '').isalnum()


@pytest.mark.django_db
@pytest.mark.parametrize('status', [404, 410])
def test_gone_subscription_deleted(sent, device, status):
    sent.side_effect = push_error(status)
    assert send_one(device, PAYLOAD) == 'gone'
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('error', [push_error(500), push_error(429), push_error(403),
                                   requests.exceptions.Timeout('slow'), ValueError('bad key')])
def test_other_failures_keep_the_device(sent, device, error):
    sent.side_effect = error
    assert send_one(device, PAYLOAD) == 'failed'
    device.refresh_from_db()
    assert device.last_used_at is None


@pytest.mark.django_db
def test_deliver_counts_each_outcome(sent):
    user = make_client('acme')
    devices = [aps_device(user, n) for n in range(3)]
    sent.side_effect = [None, push_error(410), push_error(500)]
    assert deliver(devices, PAYLOAD) == {'sent': 1, 'gone': 1, 'failed': 1}


@pytest.mark.parametrize('ua, label', [
    ('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36',
     'Chrome on Android'),
    ('Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/23.0 '
     'Chrome/115.0 Mobile Safari/537.36', 'Samsung Internet on Android'),
    ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
     'Safari on iPhone'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 '
     'Edg/131.0', 'Edge on Windows'),
    ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
     'Safari on Mac'),
    ('Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0', 'Firefox on Linux'),
    ('', 'Unknown device'),
    (None, 'Unknown device'),
])
def test_device_label(ua, label):
    assert device_label(ua) == label


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def test_generate_vapid_keys_prints_working_env_lines(capsys, settings):
    settings.VAPID_PRIVATE_KEY = ''
    call_command('generate_vapid_keys', '--subject', 'mailto:support@example.com')
    out = capsys.readouterr().out
    lines = dict(line.split('=', 1) for line in out.splitlines() if line.startswith('VAPID_'))
    assert lines['VAPID_SUBJECT'] == 'mailto:support@example.com'
    assert len(_unb64(lines['VAPID_PUBLIC_KEY'])) == 65  # the browser's applicationServerKey
    # The private key is what pywebpush accepts, and it belongs to the public key.
    vapid = Vapid.from_string(private_key=lines['VAPID_PRIVATE_KEY'])
    assert vapid.sign({'sub': 'mailto:support@example.com', 'aud': 'https://fcm.googleapis.com', 'exp': 9999999999})
    from cryptography.hazmat.primitives import serialization
    public = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    assert public == _unb64(lines['VAPID_PUBLIC_KEY'])


def test_generate_vapid_keys_needs_mailto_subject():
    from django.core.management.base import CommandError
    with pytest.raises(CommandError):
        call_command('generate_vapid_keys', '--subject', 'support@example.com')


@pytest.mark.django_db
def test_row_must_belong_to_the_right_user_table():
    user, gg = make_client('acme'), make_gg_user('owner@example.com')
    bad = [
        {'app': 'aps', 'gg_user': gg},
        {'app': 'gas_guard', 'user': user},
        {'app': 'aps', 'user': user, 'gg_user': gg},
        {'app': 'aps'},
    ]
    for n, owner in enumerate(bad):
        with pytest.raises(IntegrityError), transaction.atomic():
            PushSubscription.objects.create(endpoint=endpoint(n), p256dh=P256DH, auth=AUTH, **owner)
