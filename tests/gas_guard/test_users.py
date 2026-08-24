"""
Tests for the Gas Guard users app: self-service registration (email OTP),
verification, resend, login, logout (refresh blacklist), /me, and the
subscriber-gated notification prefs. External email is patched out.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.gas_guard.users.models import GasGuardUser, PendingRegistration
from apps.gas_guard.users.tokens import GasGuardRefreshToken

from .conftest import auth_client_for

REGISTER = '/api/gas-guard/users/register/'
VERIFY = '/api/gas-guard/users/verify-email/'
RESEND = '/api/gas-guard/users/resend-verification/'
LOGIN = '/api/gas-guard/users/login/'
LOGOUT = '/api/gas-guard/users/logout/'
ME = '/api/gas-guard/users/me/'
NOTIFS = '/api/gas-guard/users/me/notifications/'

EMAIL_PATH = 'apps.gas_guard.users.views.send_verification_email'


# ── registration ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
@patch(EMAIL_PATH, return_value=True)
def test_register_creates_pending_and_sends_code(mock_email, api_client):
    resp = api_client.post(
        REGISTER,
        {'email': 'new@example.com', 'password': 'str0ng-pass-123', 'first_name': 'Sam'},
        format='json',
    )
    assert resp.status_code == 201
    # No real user yet — only a pending row.
    assert not GasGuardUser.objects.filter(email='new@example.com').exists()
    assert PendingRegistration.objects.filter(email='new@example.com').count() == 1
    mock_email.assert_called_once()


@pytest.mark.django_db
@patch(EMAIL_PATH, return_value=True)
def test_register_rejects_duplicate_email(mock_email, api_client, gg_user):
    resp = api_client.post(
        REGISTER,
        {'email': gg_user.email, 'password': 'str0ng-pass-123'},
        format='json',
    )
    assert resp.status_code == 400


@pytest.mark.django_db
@patch(EMAIL_PATH, return_value=True)
def test_register_rejects_weak_password(mock_email, api_client):
    resp = api_client.post(
        REGISTER, {'email': 'x@example.com', 'password': '123'}, format='json'
    )
    assert resp.status_code == 400
    assert PendingRegistration.objects.count() == 0


@pytest.mark.django_db
@patch(EMAIL_PATH, return_value=False)
def test_register_rolls_back_when_email_fails(mock_email, api_client):
    resp = api_client.post(
        REGISTER, {'email': 'x@example.com', 'password': 'str0ng-pass-123'}, format='json'
    )
    assert resp.status_code == 500
    # Pending row must be cleaned up so the user can retry.
    assert PendingRegistration.objects.filter(email='x@example.com').count() == 0


# ── verification ─────────────────────────────────────────────────────────────

def _make_pending(email='new@example.com', code='123456', minutes=10):
    return PendingRegistration.objects.create(
        first_name='Sam',
        email=email,
        password='pbkdf2_sha256$fake$hash',  # not exercised in these paths
        verification_code=code,
        verification_code_expires_at=timezone.now() + timedelta(minutes=minutes),
    )


@pytest.mark.django_db
def test_verify_creates_user_and_returns_tokens(api_client):
    _make_pending()
    resp = api_client.post(
        VERIFY, {'email': 'new@example.com', 'code': '123456'}, format='json'
    )
    assert resp.status_code == 200
    assert resp.data.get('access') and resp.data.get('refresh')
    assert GasGuardUser.objects.filter(email='new@example.com').exists()
    # Pending row consumed.
    assert not PendingRegistration.objects.filter(email='new@example.com').exists()
    # New accounts start device-tier.
    assert GasGuardUser.objects.get(email='new@example.com').tier == GasGuardUser.Tier.DEVICE


@pytest.mark.django_db
def test_verify_wrong_code_rejected(api_client):
    _make_pending(code='123456')
    resp = api_client.post(
        VERIFY, {'email': 'new@example.com', 'code': '000000'}, format='json'
    )
    assert resp.status_code == 400
    assert not GasGuardUser.objects.filter(email='new@example.com').exists()


@pytest.mark.django_db
def test_verify_expired_code_rejected_and_purged(api_client):
    _make_pending(code='123456', minutes=-1)
    resp = api_client.post(
        VERIFY, {'email': 'new@example.com', 'code': '123456'}, format='json'
    )
    assert resp.status_code == 400
    assert not PendingRegistration.objects.filter(email='new@example.com').exists()


# ── resend ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
@patch(EMAIL_PATH, return_value=True)
def test_resend_rotates_code(mock_email, api_client):
    pending = _make_pending(code='111111')
    resp = api_client.post(RESEND, {'email': 'new@example.com'}, format='json')
    assert resp.status_code == 200
    pending.refresh_from_db()
    assert pending.verification_code != '111111'
    mock_email.assert_called_once()


@pytest.mark.django_db
def test_resend_without_pending_is_400(api_client):
    resp = api_client.post(RESEND, {'email': 'nobody@example.com'}, format='json')
    assert resp.status_code == 400


# ── login ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_login_success(api_client, gg_user):
    resp = api_client.post(
        LOGIN, {'email': gg_user.email, 'password': 'str0ng-pass-123'}, format='json'
    )
    assert resp.status_code == 200
    assert resp.data.get('access')
    assert resp.data['user']['email'] == gg_user.email


@pytest.mark.django_db
def test_login_wrong_password_rejected(api_client, gg_user):
    resp = api_client.post(
        LOGIN, {'email': gg_user.email, 'password': 'wrong'}, format='json'
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_login_unknown_email_rejected(api_client):
    # Regression: an unknown email must NOT 500 (the timing-mitigation hasher
    # call once crashed on check_password(pw, None)) — and must look identical
    # to a wrong-password attempt so it isn't an enumeration oracle.
    resp = api_client.post(
        LOGIN, {'email': 'ghost@example.com', 'password': 'whatever'}, format='json'
    )
    assert resp.status_code == 400


# ── /me + auth gate ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_me_requires_auth(api_client):
    assert api_client.get(ME).status_code == 401


@pytest.mark.django_db
def test_me_returns_profile(gg_client, gg_user):
    resp = gg_client.get(ME)
    assert resp.status_code == 200
    assert resp.data['email'] == gg_user.email
    assert resp.data['tier'] == GasGuardUser.Tier.DEVICE


@pytest.mark.django_db
def test_me_rejects_website_style_token(api_client, gg_user):
    # A token WITHOUT the gas_guard audience must be rejected by get_user.
    from rest_framework_simplejwt.tokens import RefreshToken
    bad = RefreshToken()  # plain simplejwt token, no gg audience/claim
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {bad.access_token}')
    assert api_client.get(ME).status_code == 401


# ── logout (refresh blacklist) ───────────────────────────────────────────────

@pytest.mark.django_db
def test_logout_blacklists_refresh(gg_client, gg_user):
    refresh = GasGuardRefreshToken.for_user(gg_user)
    resp = gg_client.post(LOGOUT, {'refresh': str(refresh)}, format='json')
    assert resp.status_code == 205


@pytest.mark.django_db
def test_logout_requires_refresh_token(gg_client):
    resp = gg_client.post(LOGOUT, {}, format='json')
    assert resp.status_code == 400


# ── notification prefs (subscriber-gated) ────────────────────────────────────

@pytest.mark.django_db
def test_notifications_forbidden_for_device_tier(gg_client):
    resp = gg_client.patch(NOTIFS, {'emailEnabled': True}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_notifications_update_for_subscriber(gg_sub_client, gg_subscriber):
    resp = gg_sub_client.patch(
        NOTIFS,
        {'emailEnabled': True, 'notifyEmail': 'alerts@example.com'},
        format='json',
    )
    assert resp.status_code == 200
    gg_subscriber.refresh_from_db()
    assert gg_subscriber.email_alerts_enabled is True
    assert gg_subscriber.notify_email == 'alerts@example.com'
