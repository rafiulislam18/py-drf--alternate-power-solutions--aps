"""
Dashboard sign-in with username OR email, and the create-client rules that keep
both identifiers pointing at exactly one account. A client's email only works
for sign-in once it's been confirmed (see apps.accounts).
"""

import re
from urllib.parse import unquote

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache

from apps.core.models import ClientProfile

PASSWORD = 'test_password_123'  # UserFactory default
TOKEN_URL = '/dashboard/auth/token/'
CREATE_URL = '/dashboard/clients/create/'


@pytest.fixture(autouse=True)
def _clear_throttles():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def sunco(db, user_factory):
    u = user_factory(username='sunco', email='ops@sunco.co.za')
    ClientProfile.objects.create(user=u, role='client', company_name='SunCo', verified_email='ops@sunco.co.za')
    return u


def _login(api_client, identifier, password=PASSWORD):
    return api_client.post(TOKEN_URL, {'username': identifier, 'password': password}, format='json')


def _create(admin_api_client, **fields):
    data = {'password': 'Sunny-Days-2026', 'confirm_password': 'Sunny-Days-2026', **fields}
    return admin_api_client.post(CREATE_URL, data, format='multipart')


@pytest.mark.django_db
class TestLoginWithEmail:

    def test_username_still_works(self, api_client, sunco):
        res = _login(api_client, 'SunCo')
        assert res.status_code == 200
        assert res.data['user_id'] == sunco.id

    def test_email_works_any_case_and_spaces(self, api_client, sunco):
        res = _login(api_client, '  OPS@SunCo.co.za ')
        assert res.status_code == 200, res.data
        assert res.data['user_id'] == sunco.id
        assert res.data['role'] == 'client'

    def test_unverified_email_does_not_sign_in(self, api_client, sunco):
        ClientProfile.objects.filter(user=sunco).update(verified_email='')
        assert _login(api_client, 'ops@sunco.co.za').status_code == 401
        assert _login(api_client, 'sunco').status_code == 200

    def test_email_with_wrong_password_rejected(self, api_client, sunco):
        res = _login(api_client, 'ops@sunco.co.za', 'wrong-password')
        assert res.status_code == 401
        assert 'username or email' in str(res.data)

    def test_unknown_email_rejected(self, api_client, sunco):
        assert _login(api_client, 'nobody@sunco.co.za').status_code == 401

    def test_inactive_account_rejected_by_email(self, api_client, sunco):
        sunco.is_active = False
        sunco.save()
        assert _login(api_client, 'ops@sunco.co.za').status_code == 401

    def test_username_match_wins_over_email(self, api_client, sunco, user_factory):
        # Legacy: a user whose *username* equals sunco's email.
        other = user_factory(username='ops@sunco.co.za', email='')
        res = _login(api_client, 'ops@sunco.co.za')
        assert res.status_code == 200
        assert res.data['user_id'] == other.id

    def test_shared_legacy_email_fails_closed(self, api_client, sunco, user_factory):
        user_factory(username='moonco', email='OPS@sunco.co.za')
        assert _login(api_client, 'ops@sunco.co.za').status_code == 401
        # Usernames are unaffected.
        assert _login(api_client, 'sunco').status_code == 200

    def test_username_and_email_share_one_throttle_budget(self, api_client, sunco):
        codes = [_login(api_client, ident, 'nope').status_code
                 for ident in ['sunco', 'ops@sunco.co.za'] * 5 + ['OPS@SUNCO.CO.ZA']]
        assert codes[:10] == [401] * 10
        assert codes[10] == 429


@pytest.mark.django_db
class TestCreateClientEmailRules:

    def test_created_client_signs_in_with_email_once_confirmed(self, admin_api_client, api_client):
        assert _create(admin_api_client, username='newco', email='Accounts@NewCo.co.za').status_code == 201
        creds = {'username': 'accounts@newco.co.za', 'password': 'Sunny-Days-2026'}
        # Not confirmed yet: only the username works.
        assert api_client.post(TOKEN_URL, creds, format='json').status_code == 401
        assert api_client.post(TOKEN_URL, {**creds, 'username': 'newco'}, format='json').status_code == 200

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ['Accounts@newco.co.za']
        token = unquote(re.search(r'token=([^"&<\s]+)', mail.outbox[0].body).group(1))
        assert api_client.post('/accounts/verify-email/', {'token': token}, format='json').status_code == 200

        res = api_client.post(TOKEN_URL, creds, format='json')
        assert res.status_code == 200
        assert res.data['user_id'] == User.objects.get(username='newco').id

    def test_created_client_without_email_gets_no_email(self, admin_api_client):
        assert _create(admin_api_client, username='newco').status_code == 201
        assert mail.outbox == []

    def test_duplicate_email_refused_any_case(self, admin_api_client, sunco):
        res = _create(admin_api_client, username='newco', email='OPS@sunco.CO.ZA')
        assert res.status_code == 400
        assert 'already exists' in str(res.data['email'])
        assert not User.objects.filter(username='newco').exists()

    def test_email_equal_to_another_username_refused(self, admin_api_client, user_factory):
        user_factory(username='ops@legacy.co.za', email='')
        res = _create(admin_api_client, username='newco', email='OPS@legacy.co.za')
        assert res.status_code == 400
        assert 'username' in str(res.data['email'])

    def test_username_equal_to_another_email_refused(self, admin_api_client, sunco):
        res = _create(admin_api_client, username='Ops@sunco.co.za')
        assert res.status_code == 400
        assert 'email' in str(res.data['username'])

    def test_blank_emails_do_not_clash(self, admin_api_client):
        assert _create(admin_api_client, username='one', email='').status_code == 201
        assert _create(admin_api_client, username='two').status_code == 201
        assert list(User.objects.filter(username__in=['one', 'two']).values_list('email', flat=True)) == ['', '']
