"""
Shared fixtures and helpers for the dashboard accounts API (apps.accounts).

Account emails normally go out on a background thread; tests/conftest.py runs
them inline, so ``mail.outbox`` is filled when the response comes back.
"""

import re
from urllib.parse import unquote

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import ClientProfile
from apps.subscription.models import Client as InverterClient, Subscription as InverterSub

PASSWORD = 'Sunny-Days-2026'
NEW_PASSWORD = 'Brand-New-Pass-77'
TOKEN_URL = '/dashboard/auth/token/'
REFRESH_URL = '/dashboard/auth/token/refresh/'
REGISTER_URL = '/accounts/register/'
VERIFY_URL = '/accounts/verify-email/'
RESEND_URL = '/accounts/resend-verification/'
FORGOT_URL = '/accounts/password/forgot/'
RESET_URL = '/accounts/password/reset/'
CHANGE_PASSWORD_URL = '/accounts/password/change/'
CHANGE_EMAIL_URL = '/accounts/email/change/'
ME_URL = '/accounts/me/'

VERIFY_SUBJECT = 'Confirm your email for the APS dashboard'
RESET_SUBJECT = 'Reset your APS dashboard password'
CLAIM_SUBJECT = 'Set up your APS dashboard account'
EXISTS_SUBJECT = 'You already have an APS dashboard account'

_TOKEN_RE = re.compile(r'[?&]token=([^"\'<>\s&]+)')


def make_client(username, email='', company_name='', verified=False, self_registered=False, phone='',
                password=PASSWORD, is_active=True):
    user = User.objects.create_user(username=username, email=email, password=password, is_active=is_active)
    ClientProfile.objects.create(
        user=user, role='client', company_name=company_name, phone=phone,
        verified_email=email if verified else '', self_registered=self_registered,
    )
    return user


def make_subscriber(email, name='Thandi Mokoena', phone='082 000 1111'):
    """A main-site subscriber (checkout Client + Subscription) with no login."""
    client = InverterClient.objects.create(name=name, email=email, phone=phone)
    return InverterSub.objects.create(
        client=client, address='3 Beach Rd, Muizenberg', payfast_token='tok-1', is_active=True,
        subscription_length=1, last_payment_date=timezone.now(),
    )


def anon():
    return APIClient()


def bearer(access):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
    return api


def login(identifier, password=PASSWORD):
    return anon().post(TOKEN_URL, {'username': identifier, 'password': password}, format='json')


def post(url, data, api=None, **extra):
    return (api or anon()).post(url, data, format='json', **extra)


def token_in(message):
    """The ``?token=`` value of the link in an account email."""
    match = _TOKEN_RE.search(message.body)
    assert match, message.body
    return unquote(match.group(1))


def emails_to(outbox, address, subject=None):
    return [m for m in outbox if m.to == [address] and (subject is None or m.subject == subject)]


def clear_verify_cooldown(user, email=None):
    cache.delete(f'accounts:verify-sent:{user.pk}:{(email or user.email).lower()}')


@pytest.fixture(autouse=True)
def _isolate(settings, tmp_path):
    cache.clear()  # throttles and the verification-email cooldown live here
    settings.MEDIA_ROOT = str(tmp_path)
    yield
    cache.clear()


@pytest.fixture
def verified_client(db):
    """Made by APS, email confirmed: signs in with username or email."""
    return make_client('sunco', email='ops@sunco.co.za', company_name='SunCo', verified=True, phone='021 000 0000')


@pytest.fixture
def unverified_client(db):
    """Made by APS with an email nobody has confirmed yet."""
    return make_client('moonco', email='ops@moonco.co.za', company_name='MoonCo')
