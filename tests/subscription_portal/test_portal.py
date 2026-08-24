"""
Tests for the public Manage-Subscriptions portal.

Covers the OTP lifecycle (send / reuse / verify / wrong / expired / attempt-cap),
the scoped-token gate on the protected endpoints, cross-app subscription
listing, and the cancel path — including the critical ownership check that a
valid token can't cancel another person's subscription.

PayFast's real cancel call is patched out so tests never hit the network; we
assert it's invoked with the right token and that our DB state flips correctly.
"""

from datetime import timedelta
from unittest.mock import patch

import jwt
import pytest
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.subscription.models import Client as InverterClient, Subscription as InverterSub
from apps.request_solar_cleaning.models import (
    Client as SolarClient,
    Subscription as SolarSub,
)
from apps.subscription_portal.models import OTP_MAX_ATTEMPTS, PortalOTP
from apps.subscription_portal.providers import parse_ref
from apps.subscription_portal.throttling import OtpRequestThrottle
from apps.subscription_portal.tokens import ACCESS_TTL, SCOPE, issue_portal_token

REQUEST_URL = '/subscription-portal/request-otp/'
VERIFY_URL = '/subscription-portal/verify-otp/'
LIST_URL = '/subscription-portal/subscriptions/'
CANCEL_URL = '/subscription-portal/cancel/'

EMAIL = 'subscriber@example.com'


@pytest.fixture(autouse=True)
def _clear_cache():
    """Throttle state lives in the cache; reset between tests so limits don't
    bleed across cases (except the dedicated throttle test)."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def inverter_sub(db):
    client = InverterClient.objects.create(name='Sub One', email=EMAIL, phone='0110000000')
    return InverterSub.objects.create(
        client=client,
        address='12 Main Rd, Cape Town',
        payfast_token='tok-inv-123',
        is_active=True,
        subscription_length=4,
        last_payment_date=timezone.now(),
    )


@pytest.fixture
def solar_sub(db):
    client = SolarClient.objects.create(name='Sub One', email=EMAIL, phone='0110000000')
    return SolarSub.objects.create(
        client=client,
        address='12 Main Rd, Cape Town',
        payfast_token='tok-sol-456',
        is_active=True,
        subscription_length=2,
    )


def _auth(token):
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


# ── request-otp ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_request_otp_sends_code_for_known_email(api_client, inverter_sub):
    resp = api_client.post(REQUEST_URL, {'email': EMAIL}, format='json')
    assert resp.status_code == 200
    assert PortalOTP.objects.filter(email=EMAIL, consumed=False).count() == 1


@pytest.mark.django_db
def test_request_otp_neutral_and_silent_for_unknown_email(api_client):
    resp = api_client.post(REQUEST_URL, {'email': 'nobody@example.com'}, format='json')
    # Same neutral 200 as a known email — no enumeration oracle …
    assert resp.status_code == 200
    # … but no code is actually created for a non-subscriber.
    assert PortalOTP.objects.count() == 0


@pytest.mark.django_db
def test_request_otp_case_insensitive_email(api_client, inverter_sub):
    resp = api_client.post(REQUEST_URL, {'email': EMAIL.upper()}, format='json')
    assert resp.status_code == 200
    assert PortalOTP.objects.filter(email=EMAIL).count() == 1


@pytest.mark.django_db
def test_request_otp_reuses_live_code(api_client, inverter_sub):
    api_client.post(REQUEST_URL, {'email': EMAIL}, format='json')
    api_client.post(REQUEST_URL, {'email': EMAIL}, format='json')
    # Second request within the live window must NOT mint a second code.
    assert PortalOTP.objects.filter(email=EMAIL, consumed=False).count() == 1


# ── verify-otp ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_verify_otp_happy_path_returns_token(api_client, inverter_sub):
    otp = PortalOTP.objects.create(
        email=EMAIL, code='123456', expires_at=timezone.now() + timedelta(minutes=30)
    )
    resp = api_client.post(VERIFY_URL, {'email': EMAIL, 'code': '123456'}, format='json')
    assert resp.status_code == 200
    assert resp.data.get('access')
    otp.refresh_from_db()
    assert otp.consumed is True  # single-use


@pytest.mark.django_db
def test_verify_otp_wrong_code_increments_attempts(api_client):
    PortalOTP.objects.create(
        email=EMAIL, code='123456', expires_at=timezone.now() + timedelta(minutes=30)
    )
    resp = api_client.post(VERIFY_URL, {'email': EMAIL, 'code': '000000'}, format='json')
    assert resp.status_code == 400
    assert PortalOTP.objects.get(email=EMAIL).attempts == 1


@pytest.mark.django_db
def test_verify_otp_expired_code_rejected(api_client):
    PortalOTP.objects.create(
        email=EMAIL, code='123456', expires_at=timezone.now() - timedelta(minutes=1)
    )
    resp = api_client.post(VERIFY_URL, {'email': EMAIL, 'code': '123456'}, format='json')
    assert resp.status_code == 400
    assert 'access' not in resp.data


@pytest.mark.django_db
def test_verify_otp_attempt_cap_burns_code(api_client):
    otp = PortalOTP.objects.create(
        email=EMAIL, code='123456',
        expires_at=timezone.now() + timedelta(minutes=30),
        attempts=OTP_MAX_ATTEMPTS - 1,
    )
    # This wrong guess hits the cap → burned even with the correct code after.
    api_client.post(VERIFY_URL, {'email': EMAIL, 'code': '000000'}, format='json')
    otp.refresh_from_db()
    assert otp.attempts == OTP_MAX_ATTEMPTS
    assert otp.is_live is False
    # Even the correct code no longer works.
    resp = api_client.post(VERIFY_URL, {'email': EMAIL, 'code': '123456'}, format='json')
    assert resp.status_code == 400


# ── subscriptions (protected) ────────────────────────────────────────────────

@pytest.mark.django_db
def test_subscriptions_requires_token(api_client, inverter_sub):
    resp = api_client.get(LIST_URL)
    assert resp.status_code == 401


@pytest.mark.django_db
def test_subscriptions_lists_across_both_apps(api_client, inverter_sub, solar_sub):
    token = issue_portal_token(EMAIL)
    resp = api_client.get(LIST_URL, **_auth(token))
    assert resp.status_code == 200
    subs = resp.data['subscriptions']
    assert len(subs) == 2
    providers = {s['provider'] for s in subs}
    assert providers == {'inverter', 'solar'}


@pytest.mark.django_db
def test_subscriptions_only_returns_token_email_rows(api_client, inverter_sub):
    # A different email's token sees nothing of EMAIL's subs.
    token = issue_portal_token('someone.else@example.com')
    resp = api_client.get(LIST_URL, **_auth(token))
    assert resp.status_code == 200
    assert resp.data['subscriptions'] == []


# ── cancel (protected + ownership) ───────────────────────────────────────────

@pytest.mark.django_db
@patch('apps.subscription_portal.views.cancel_payfast_subscription', return_value=True)
def test_cancel_deactivates_and_calls_payfast(mock_cancel, api_client, inverter_sub):
    token = issue_portal_token(EMAIL)
    ref = _get_ref(api_client, token, 'inverter')

    resp = api_client.post(CANCEL_URL, {'ref': ref}, format='json', **_auth(token))
    assert resp.status_code == 200
    mock_cancel.assert_called_once_with('tok-inv-123')
    inverter_sub.refresh_from_db()
    assert inverter_sub.is_active is False  # local deactivate


@pytest.mark.django_db
@patch('apps.subscription_portal.views.cancel_payfast_subscription', return_value=True)
def test_cancel_rejects_non_owner(mock_cancel, api_client, inverter_sub):
    # Attacker holds a valid token for THEIR email, tries to cancel EMAIL's sub
    # using EMAIL's ref (obtained here for the test).
    owner_token = issue_portal_token(EMAIL)
    ref = _get_ref(api_client, owner_token, 'inverter')

    attacker_token = issue_portal_token('attacker@example.com')
    resp = api_client.post(CANCEL_URL, {'ref': ref}, format='json', **_auth(attacker_token))
    assert resp.status_code == 403
    mock_cancel.assert_not_called()
    inverter_sub.refresh_from_db()
    assert inverter_sub.is_active is True  # untouched


@pytest.mark.django_db
@patch('apps.subscription_portal.views.cancel_payfast_subscription', return_value=False)
def test_cancel_leaves_db_untouched_if_payfast_fails(mock_cancel, api_client, inverter_sub):
    token = issue_portal_token(EMAIL)
    ref = _get_ref(api_client, token, 'inverter')

    resp = api_client.post(CANCEL_URL, {'ref': ref}, format='json', **_auth(token))
    assert resp.status_code == 502
    inverter_sub.refresh_from_db()
    assert inverter_sub.is_active is True  # not deactivated when upstream fails


@pytest.mark.django_db
def test_cancel_rejects_tampered_ref(api_client, inverter_sub):
    token = issue_portal_token(EMAIL)
    resp = api_client.post(CANCEL_URL, {'ref': 'garbage:1'}, format='json', **_auth(token))
    assert resp.status_code == 400


# ── throttling ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_request_otp_throttled_after_5_in_window(api_client, inverter_sub):
    cache.clear()
    codes = [api_client.post(REQUEST_URL, {'email': EMAIL}, format='json').status_code
             for _ in range(6)]
    # 5 allowed per 30-min window, the 6th is throttled.
    assert codes[:5] == [200] * 5
    assert codes[5] == 429


# ── missing / malformed input ────────────────────────────────────────────────

@pytest.mark.django_db
def test_request_otp_missing_email_is_400(api_client):
    resp = api_client.post(REQUEST_URL, {}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_verify_otp_missing_fields_is_400(api_client):
    assert api_client.post(VERIFY_URL, {'email': EMAIL}, format='json').status_code == 400
    assert api_client.post(VERIFY_URL, {'code': '123456'}, format='json').status_code == 400


@pytest.mark.django_db
def test_cancel_missing_ref_is_400(api_client, inverter_sub):
    token = issue_portal_token(EMAIL)
    resp = api_client.post(CANCEL_URL, {}, format='json', **_auth(token))
    assert resp.status_code == 400


# ── access-token gate (the core auth guarantee) ──────────────────────────────

def _expired_token(email):
    """A correctly-signed token whose exp is already in the past."""
    now = timezone.now() - ACCESS_TTL - timedelta(minutes=1)
    return jwt.encode(
        {
            'email': email,
            'scope': SCOPE,
            'iat': int(now.timestamp()),
            'exp': int((now + ACCESS_TTL).timestamp()),
        },
        settings.SECRET_KEY,
        algorithm='HS256',
    )


@pytest.mark.django_db
def test_subscriptions_rejects_expired_token(api_client, inverter_sub):
    resp = api_client.get(LIST_URL, **_auth(_expired_token(EMAIL)))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_cancel_rejects_expired_token(api_client, inverter_sub):
    resp = api_client.post(CANCEL_URL, {'ref': 'x'}, format='json', **_auth(_expired_token(EMAIL)))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_subscriptions_rejects_wrong_scope_token(api_client, inverter_sub):
    now = timezone.now()
    bad = jwt.encode(
        {'email': EMAIL, 'scope': 'something_else',
         'iat': int(now.timestamp()), 'exp': int((now + ACCESS_TTL).timestamp())},
        settings.SECRET_KEY, algorithm='HS256',
    )
    resp = api_client.get(LIST_URL, **_auth(bad))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_subscriptions_rejects_tampered_signature(api_client, inverter_sub):
    # Signed with the wrong key → invalid signature.
    now = timezone.now()
    forged = jwt.encode(
        {'email': EMAIL, 'scope': SCOPE,
         'iat': int(now.timestamp()), 'exp': int((now + ACCESS_TTL).timestamp())},
        'not-the-real-secret', algorithm='HS256',
    )
    resp = api_client.get(LIST_URL, **_auth(forged))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_subscriptions_rejects_malformed_auth_header(api_client, inverter_sub):
    # No "Bearer " prefix.
    resp = api_client.get(LIST_URL, HTTP_AUTHORIZATION='token abc')
    assert resp.status_code == 401


# ── cancel: already-inactive ─────────────────────────────────────────────────

@pytest.mark.django_db
@patch('apps.subscription_portal.views.cancel_payfast_subscription', return_value=True)
def test_cancel_already_inactive_is_400(mock_cancel, api_client, inverter_sub):
    inverter_sub.is_active = False
    inverter_sub.save(update_fields=['is_active'])
    token = issue_portal_token(EMAIL)
    ref = _get_ref(api_client, token, 'inverter')
    resp = api_client.post(CANCEL_URL, {'ref': ref}, format='json', **_auth(token))
    assert resp.status_code == 400
    mock_cancel.assert_not_called()


# ── unit: opaque ref + throttle rate parser ──────────────────────────────────

@pytest.mark.django_db
def test_parse_ref_round_trips(inverter_sub):
    token = issue_portal_token(EMAIL)
    from rest_framework.test import APIClient
    ref = _get_ref(APIClient(), token, 'inverter')
    provider, pk = parse_ref(ref)
    assert provider == 'inverter'
    assert pk == inverter_sub.pk


def test_parse_ref_rejects_tampered_ref():
    from django.core.signing import BadSignature
    with pytest.raises((BadSignature, ValueError)):
        parse_ref('totally-not-a-signed-ref')


def test_throttle_rate_parser_reads_full_window():
    # The custom parse_rate must read "5/30m" as 5 per 1800s, not 5 per 60s.
    num, seconds = OtpRequestThrottle().parse_rate('5/30m')
    assert (num, seconds) == (5, 1800)


# ── helper ───────────────────────────────────────────────────────────────────

def _get_ref(api_client, token, provider):
    """Fetch the opaque ref for a provider's subscription via the list endpoint."""
    resp = api_client.get(LIST_URL, **_auth(token))
    for s in resp.data['subscriptions']:
        if s['provider'] == provider:
            return s['ref']
    raise AssertionError(f'no {provider} subscription in list')
