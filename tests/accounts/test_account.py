"""
Signed-in account settings, token rules, roles and throttles (apps.accounts,
apps.core.roles).

Rules covered: changing the email needs the current password and only takes
effect once the new address is confirmed (and not if another account took it
meanwhile); clients may edit their name and phone but nothing else, staff edit
nothing here; dashboard tokens must carry the current password version; a
profile-less login is a client unless it's Django staff; and the public
endpoints are rate-limited per IP and per target email.
"""

import pytest
from django.contrib.auth.models import User
from django.core import mail
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.tokens import password_version
from apps.core.models import ClientProfile
from apps.core.roles import get_role

from .conftest import (
    CHANGE_EMAIL_URL,
    FORGOT_URL,
    ME_URL,
    PASSWORD,
    REFRESH_URL,
    REGISTER_URL,
    RESEND_URL,
    VERIFY_SUBJECT,
    VERIFY_URL,
    bearer,
    login,
    make_client,
    post,
    token_in,
)


def _signed_in(identifier='sunco'):
    res = login(identifier)
    assert res.status_code == 200, res.data
    return bearer(res.data['access'])


def _change_email(api, email, current=PASSWORD):
    return post(CHANGE_EMAIL_URL, {'email': email, 'current_password': current}, api=api)


# ── change email ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestChangeEmail:

    def test_requires_current_password(self, verified_client):
        api = _signed_in()
        wrong = _change_email(api, 'new@sunco.co.za', current='Not-The-One-11')
        assert wrong.status_code == 400
        assert 'current_password' in wrong.data
        missing = post(CHANGE_EMAIL_URL, {'email': 'new@sunco.co.za'}, api=api)
        assert missing.status_code == 400
        assert 'current_password' in missing.data
        verified_client.client_profile.refresh_from_db()
        assert verified_client.client_profile.pending_email == ''
        assert mail.outbox == []

    @pytest.mark.parametrize('taken', ['OPS@MoonCo.co.za', 'moonco'])
    def test_email_or_username_of_another_account_refused(self, verified_client, unverified_client, taken):
        if taken == 'moonco':
            # A legacy account whose username is an email address.
            make_client('legacy@moonco.co.za')
            taken = 'Legacy@MoonCo.co.za'
        res = _change_email(_signed_in(), taken)
        assert res.status_code == 400
        assert 'email' in res.data
        assert mail.outbox == []

    def test_current_verified_email_refused(self, verified_client):
        res = _change_email(_signed_in(), 'OPS@sunco.co.za')
        assert res.status_code == 400
        assert 'email' in res.data

    def test_change_is_pending_until_confirmed(self, verified_client):
        api = _signed_in()
        res = _change_email(api, ' New@SunCo.co.za ')
        assert res.status_code == 200, res.data
        assert res.data['email'] == 'ops@sunco.co.za'
        assert res.data['pending_email'] == 'new@sunco.co.za'

        verified_client.refresh_from_db()
        assert verified_client.email == 'ops@sunco.co.za'
        assert verified_client.client_profile.email_verified is True  # old address still works
        assert login('ops@sunco.co.za').status_code == 200
        assert login('new@sunco.co.za').status_code == 401

        assert [(m.to, m.subject) for m in mail.outbox] == [(['new@sunco.co.za'], VERIFY_SUBJECT)]
        assert api.get(ME_URL).data['pending_email'] == 'new@sunco.co.za'

    def test_confirming_swaps_the_email(self, verified_client):
        _change_email(_signed_in(), 'new@sunco.co.za')
        res = post(VERIFY_URL, {'token': token_in(mail.outbox[0])})
        assert res.status_code == 200, res.data
        assert res.data['email'] == 'new@sunco.co.za'

        verified_client.refresh_from_db()
        profile = verified_client.client_profile
        assert verified_client.email == 'new@sunco.co.za'
        assert profile.verified_email == 'new@sunco.co.za'
        assert profile.pending_email == ''
        assert login('new@sunco.co.za').status_code == 200
        assert login('ops@sunco.co.za').status_code == 401
        assert login('sunco').status_code == 200  # the username always works

    def test_old_address_link_is_dead_after_swap(self, verified_client):
        from apps.accounts.tokens import make_verify_token

        old = make_verify_token(verified_client, 'ops@sunco.co.za')
        _change_email(_signed_in(), 'new@sunco.co.za')
        assert post(VERIFY_URL, {'token': token_in(mail.outbox[0])}).status_code == 200
        assert post(VERIFY_URL, {'token': old}).status_code == 400

    def test_swap_refused_if_address_taken_meanwhile(self, verified_client):
        _change_email(_signed_in(), 'new@sunco.co.za')
        token = token_in(mail.outbox[0])
        make_client('squatter', email='New@SunCo.co.za')

        res = post(VERIFY_URL, {'token': token})
        assert res.status_code == 400
        verified_client.refresh_from_db()
        assert verified_client.email == 'ops@sunco.co.za'
        assert verified_client.client_profile.email_verified is True

    def test_delete_clears_pending_and_kills_its_link(self, verified_client):
        api = _signed_in()
        _change_email(api, 'new@sunco.co.za')
        token = token_in(mail.outbox[0])

        res = api.delete(CHANGE_EMAIL_URL)
        assert res.status_code == 200
        assert res.data['pending_email'] == ''
        verified_client.client_profile.refresh_from_db()
        assert verified_client.client_profile.pending_email == ''
        assert post(VERIFY_URL, {'token': token}).status_code == 400
        verified_client.refresh_from_db()
        assert verified_client.email == 'ops@sunco.co.za'

    def test_newer_request_replaces_the_pending_address(self, verified_client):
        api = _signed_in()
        _change_email(api, 'first@sunco.co.za')
        first = token_in(mail.outbox[0])
        _change_email(api, 'second@sunco.co.za')
        assert post(VERIFY_URL, {'token': first}).status_code == 400
        assert post(VERIFY_URL, {'token': token_in(mail.outbox[1])}).status_code == 200
        verified_client.refresh_from_db()
        assert verified_client.email == 'second@sunco.co.za'

    def test_reconfirming_current_unverified_email(self, unverified_client):
        api = _signed_in('moonco')
        res = _change_email(api, unverified_client.email)
        assert res.status_code == 200
        assert res.data['pending_email'] == ''
        assert [m.to for m in mail.outbox] == [[unverified_client.email]]
        assert post(VERIFY_URL, {'token': token_in(mail.outbox[0])}).status_code == 200
        assert login(unverified_client.email).status_code == 200

    def test_staff_change_email_refused(self, staff_user):
        api = APIClient()
        api.force_authenticate(staff_user)
        assert _change_email(api, 'x@aps.example', current='test_password_123').status_code == 403

    def test_requires_sign_in(self, db):
        assert post(CHANGE_EMAIL_URL, {'email': 'x@y.co', 'current_password': 'x'}).status_code == 401
        assert APIClient().delete(CHANGE_EMAIL_URL).status_code == 401


# ── me ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestMe:

    def test_client_details(self, verified_client):
        res = _signed_in().get(ME_URL)
        assert res.status_code == 200
        assert res.data == {
            'username': 'sunco', 'email': 'ops@sunco.co.za', 'email_verified': True, 'pending_email': '',
            'role': 'client', 'company_name': 'SunCo', 'phone': '021 000 0000', 'image': None,
        }

    def test_staff_details(self, staff_user):
        api = APIClient()
        api.force_authenticate(staff_user)
        res = api.get(ME_URL)
        assert res.status_code == 200
        assert res.data['role'] == 'admin'
        assert res.data['company_name'] == ''

    def test_client_can_edit_name_and_phone(self, verified_client):
        api = _signed_in()
        res = api.patch(ME_URL, {'company_name': '  SunCo   Holdings ', 'phone': ' 082  111 2222 '}, format='json')
        assert res.status_code == 200, res.data
        assert res.data['company_name'] == 'SunCo Holdings'
        assert res.data['phone'] == '082 111 2222'
        assert api.patch(ME_URL, {'phone': ''}, format='json').data['phone'] == ''

    def test_client_cannot_edit_anything_else(self, verified_client):
        res = _signed_in().patch(ME_URL, {'role': 'admin', 'email': 'evil@x.example', 'username': 'root',
                                          'verified_email': 'evil@x.example', 'is_staff': True}, format='json')
        assert res.status_code == 200
        verified_client.refresh_from_db()
        profile = verified_client.client_profile
        assert (verified_client.username, verified_client.email, verified_client.is_staff) == \
            ('sunco', 'ops@sunco.co.za', False)
        assert (profile.role, profile.verified_email) == ('client', 'ops@sunco.co.za')

    def test_blank_company_name_refused(self, verified_client):
        res = _signed_in().patch(ME_URL, {'company_name': '   '}, format='json')
        assert res.status_code == 400
        assert 'company_name' in res.data

    def test_staff_patch_forbidden(self, staff_user):
        api = APIClient()
        api.force_authenticate(staff_user)
        assert api.patch(ME_URL, {'company_name': 'APS'}, format='json').status_code == 403
        assert not ClientProfile.objects.filter(user=staff_user).exists()

    def test_requires_sign_in(self, db):
        assert APIClient().get(ME_URL).status_code == 401


# ── dashboard tokens ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTokens:

    def test_sign_in_response(self, verified_client):
        res = login('sunco')
        assert res.status_code == 200
        assert res.data['user_id'] == verified_client.pk
        assert res.data['username'] == 'sunco'
        assert res.data['role'] == 'client'
        assert RefreshToken(res.data['refresh'])['pv'] == password_version(verified_client)

    def test_refresh_keeps_working(self, verified_client):
        tokens = login('sunco').data
        res = post(REFRESH_URL, {'refresh': tokens['refresh']})
        assert res.status_code == 200
        assert bearer(res.data['access']).get(ME_URL).status_code == 200

    def test_access_token_without_pv_rejected(self, verified_client):
        refresh = RefreshToken.for_user(verified_client)
        res = bearer(str(refresh.access_token)).get(ME_URL)
        assert res.status_code == 401
        assert 'sign in again' in res.data['detail']

    def test_refresh_token_without_pv_rejected(self, verified_client):
        refresh = RefreshToken.for_user(verified_client)
        assert post(REFRESH_URL, {'refresh': str(refresh)}).status_code == 401

    def test_token_with_stale_pv_rejected(self, verified_client):
        refresh = RefreshToken.for_user(verified_client)
        refresh['pv'] = password_version(verified_client)
        verified_client.set_password('Changed-In-Admin-3')
        verified_client.save()
        assert bearer(str(refresh.access_token)).get(ME_URL).status_code == 401
        assert post(REFRESH_URL, {'refresh': str(refresh)}).status_code == 401

    def test_inactive_user_refresh_rejected(self, verified_client):
        tokens = login('sunco').data
        verified_client.is_active = False
        verified_client.save()
        assert post(REFRESH_URL, {'refresh': tokens['refresh']}).status_code == 401
        assert bearer(tokens['access']).get(ME_URL).status_code == 401

    def test_garbage_refresh_rejected(self, db):
        assert post(REFRESH_URL, {'refresh': 'garbage'}).status_code == 401

    def test_staff_sign_in_by_email_without_profile(self, staff_user):
        staff_user.email = 'tech@aps.example'
        staff_user.save()
        res = login('Tech@APS.example', 'test_password_123')
        assert res.status_code == 200
        assert res.data['role'] == 'admin'


# ── roles ───────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRoles:

    def test_profileless_non_staff_user_is_client(self, user):
        assert get_role(user) == 'client'
        res = login('testuser', 'test_password_123')
        assert res.status_code == 200
        assert res.data['role'] == 'client'
        api = bearer(res.data['access'])
        assert api.get('/dashboard/clients/').status_code == 403
        assert api.post('/dashboard/clients/create/', {'username': 'x'}, format='json').status_code == 403
        assert api.get('/client-portal/me/').status_code == 200

    def test_profileless_staff_is_admin(self, staff_user, admin_user):
        assert get_role(staff_user) == 'admin'
        assert get_role(admin_user) == 'admin'
        res = login('staff', 'test_password_123')
        assert res.data['role'] == 'admin'
        api = bearer(res.data['access'])
        assert api.get('/dashboard/clients/').status_code == 200
        assert api.get('/client-portal/me/').status_code == 403

    def test_profile_decides_over_staff_flag(self, db):
        office = make_client('office')
        ClientProfile.objects.filter(user=office).update(role='admin')
        staff_client = make_client('staffclient')
        User.objects.filter(pk=staff_client.pk).update(is_staff=True)
        assert get_role(User.objects.get(pk=office.pk)) == 'admin'
        assert get_role(User.objects.get(pk=staff_client.pk)) == 'client'
        assert bearer(login('office').data['access']).get('/dashboard/clients/').status_code == 200
        assert bearer(login('staffclient').data['access']).get('/dashboard/clients/').status_code == 403

    def test_self_registered_client_never_admin(self, db):
        post(REGISTER_URL, {'name': 'Me', 'email': 'me@home.example', 'password': PASSWORD,
                            'confirm_password': PASSWORD})
        post(VERIFY_URL, {'token': token_in(mail.outbox[0])})
        api = bearer(login('me@home.example').data['access'])
        assert api.get('/dashboard/clients/').status_code == 403


# ── throttles ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestThrottles:

    def test_forgot_password_capped_per_email(self, verified_client):
        codes = [post(FORGOT_URL, {'email': e}).status_code
                 for e in ['ops@sunco.co.za', 'OPS@SunCo.co.za ', 'ops@sunco.co.za', 'ops@sunco.co.za',
                           'ops@sunco.co.za', 'Ops@sunco.co.za']]
        assert codes == [200] * 5 + [429]
        assert len(mail.outbox) == 5
        # Another address from the same IP is still fine.
        assert post(FORGOT_URL, {'email': 'other@sunco.co.za'}).status_code == 200

    def test_email_budget_shared_by_sending_endpoints(self, db):
        target = {'email': 'victim@home.example'}
        for _ in range(3):
            assert post(FORGOT_URL, target).status_code == 200
        for _ in range(2):
            assert post(RESEND_URL, target).status_code == 200
        assert post(REGISTER_URL, {**target, 'name': 'X', 'password': PASSWORD,
                                   'confirm_password': PASSWORD}).status_code == 429

    def test_public_endpoints_capped_per_ip(self, db):
        for i in range(30):
            assert post(FORGOT_URL, {'email': f'n{i}@nowhere.example'}).status_code == 200
        assert post(FORGOT_URL, {'email': 'fresh@nowhere.example'}).status_code == 429
        assert post(VERIFY_URL, {'token': 'x'}).status_code == 429
        # Another IP has its own budget.
        assert post(FORGOT_URL, {'email': 'fresh@nowhere.example'}, REMOTE_ADDR='10.0.0.9').status_code == 200
