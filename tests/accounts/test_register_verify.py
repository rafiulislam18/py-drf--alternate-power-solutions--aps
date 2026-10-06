"""
Public sign-up, email confirmation and signing in (apps.accounts +
/dashboard/auth/token/).

Rules covered: a sign-up creates an unverified self-registered client that
can't sign in (403 + a fresh confirmation email, at most one per cooldown)
until its email is confirmed; signing up with a taken email looks identical
but emails the existing owner instead; a confirmed email becomes a sign-in
identifier; emailed links are tamper-proof and expire; resends never reveal
whether an email has an account.
"""

import pytest
from django.contrib.auth.models import User
from django.core import mail, signing

from apps.accounts import tokens
from apps.core.models import ClientProfile

from .conftest import (
    EXISTS_SUBJECT,
    PASSWORD,
    REGISTER_URL,
    RESEND_URL,
    VERIFY_SUBJECT,
    VERIFY_URL,
    clear_verify_cooldown,
    emails_to,
    login,
    make_client,
    post,
    token_in,
)


def _register(**fields):
    data = {'name': 'Acme Properties', 'email': 'new@acme.example', 'phone': '021 555 0100',
            'password': PASSWORD, 'confirm_password': PASSWORD, **fields}
    return post(REGISTER_URL, data)


def _verify(token):
    return post(VERIFY_URL, {'token': token})


# ── register ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRegister:

    def test_creates_unverified_self_registered_client(self):
        res = _register(name='  Acme   Properties ', email=' New@Acme.Example ', phone=' 021  555 0100 ')
        assert res.status_code == 201, res.data
        assert res.data['email'] == 'new@acme.example'
        assert 'Check your inbox' in res.data['detail']

        user = User.objects.get(email='new@acme.example')
        assert user.username == 'new'  # from the local part, never the email itself
        assert not user.is_staff and not user.is_superuser
        assert user.check_password(PASSWORD)
        profile = user.client_profile
        assert profile.role == 'client'
        assert profile.self_registered is True
        assert profile.verified_email == ''
        assert profile.email_verified is False
        assert profile.company_name == 'Acme Properties'
        assert profile.phone == '021 555 0100'

        sent = emails_to(mail.outbox, 'new@acme.example', VERIFY_SUBJECT)
        assert len(sent) == 1
        assert '/dashboard/verify-email?token=' in sent[0].body
        assert len(mail.outbox) == 1

    def test_username_gets_suffix_when_local_part_taken(self):
        make_client('new')
        assert _register().status_code == 201
        username = User.objects.get(email='new@acme.example').username
        assert username.startswith('new-') and username != 'new'

    def test_cannot_sign_in_until_verified(self):
        _register()
        for identifier in ('new@acme.example', 'new'):
            res = login(identifier)
            assert res.status_code == 403, res.data
            assert 'confirm your email' in res.data['detail']
            assert 'access' not in res.data
        # Sign-up just sent a link, so the cooldown holds back more copies.
        assert len(mail.outbox) == 1

    def test_blocked_sign_in_resends_link_after_cooldown(self):
        _register()
        user = User.objects.get(email='new@acme.example')
        clear_verify_cooldown(user)
        assert login('new@acme.example').status_code == 403
        assert login('new@acme.example').status_code == 403
        assert len(emails_to(mail.outbox, 'new@acme.example', VERIFY_SUBJECT)) == 2  # sign-up + one resend

    def test_wrong_password_is_plain_401_and_sends_nothing(self):
        _register()
        clear_verify_cooldown(User.objects.get(email='new@acme.example'))
        res = login('new@acme.example', 'Not-The-Password-1')
        assert res.status_code == 401
        assert len(mail.outbox) == 1

    def test_duplicate_email_same_reply_no_new_user_and_owner_told(self):
        owner = make_client('sunco', email='ops@sunco.co.za', company_name='SunCo', verified=True)
        fresh = _register(email='someone@else.example')
        mail.outbox.clear()

        res = _register(email='OPS@SunCo.co.za', password='Other-Pass-2026', confirm_password='Other-Pass-2026')
        assert res.status_code == 201
        assert res.data == {**fresh.data, 'email': 'ops@sunco.co.za'}
        assert User.objects.filter(email__iexact='ops@sunco.co.za').count() == 1
        owner.refresh_from_db()
        assert owner.check_password(PASSWORD)  # untouched

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ['ops@sunco.co.za']
        assert mail.outbox[0].subject == EXISTS_SUBJECT
        assert '/dashboard/forgot-password' in mail.outbox[0].body

    def test_email_matching_a_legacy_username_creates_nothing(self):
        make_client('ops@legacy.co.za')
        res = _register(email='ops@legacy.co.za')
        assert res.status_code == 201
        assert User.objects.count() == 1
        assert mail.outbox == []

    @pytest.mark.parametrize('password', ['short', 'password123', '12345678901', 'new@acme.example'])
    def test_weak_password_rejected_with_field_name(self, password):
        res = _register(password=password, confirm_password=password)
        assert res.status_code == 400
        assert 'password' in res.data
        assert res.data['detail']
        assert not User.objects.exists()
        assert mail.outbox == []

    def test_mismatched_confirm_rejected_with_field_name(self):
        res = _register(confirm_password='Sunny-Days-2027')
        assert res.status_code == 400
        assert 'confirm_password' in res.data
        assert not User.objects.exists()

    @pytest.mark.parametrize('field,value', [('name', '   '), ('email', 'not-an-email'), ('email', '')])
    def test_bad_fields_rejected_with_field_name(self, field, value):
        res = _register(**{field: value})
        assert res.status_code == 400
        assert field in res.data
        assert not User.objects.exists()

    def test_client_cannot_choose_role_or_verified_state(self):
        res = _register(role='admin', is_staff=True, verified_email='new@acme.example', self_registered=False)
        assert res.status_code == 201
        user = User.objects.get(email='new@acme.example')
        assert not user.is_staff
        assert user.client_profile.role == 'client'
        assert user.client_profile.email_verified is False
        assert user.client_profile.self_registered is True


# ── verify email ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestVerifyEmail:

    def test_link_verifies_then_email_signs_in(self):
        _register()
        res = _verify(token_in(mail.outbox[0]))
        assert res.status_code == 200, res.data
        assert res.data['email'] == 'new@acme.example'
        profile = ClientProfile.objects.get(user__email='new@acme.example')
        assert profile.email_verified is True

        signed_in = login('NEW@acme.example ')
        assert signed_in.status_code == 200, signed_in.data
        assert signed_in.data['role'] == 'client'
        assert signed_in.data['email'] == 'new@acme.example'
        assert signed_in.data['company_name'] == 'Acme Properties'
        assert set(signed_in.data) >= {'access', 'refresh', 'role', 'user_id', 'username', 'email',
                                       'company_name', 'image'}

    def test_link_can_be_clicked_twice(self):
        _register()
        token = token_in(mail.outbox[0])
        assert _verify(token).status_code == 200
        assert _verify(token).status_code == 200

    @pytest.mark.parametrize('mangle', [
        lambda t: t[:-2] + ('AA' if not t.endswith('AA') else 'BB'),
        lambda t: 'x' + t,
        lambda t: 'garbage',
    ])
    def test_tampered_token_rejected(self, mangle):
        _register()
        res = _verify(mangle(token_in(mail.outbox[0])))
        assert res.status_code == 400
        assert ClientProfile.objects.get().email_verified is False

    def test_expired_token_rejected(self, monkeypatch, unverified_client):
        real_ts = signing.TimestampSigner.timestamp
        four_days = 4 * 24 * 60 * 60
        monkeypatch.setattr(signing.TimestampSigner, 'timestamp',
                            lambda self: signing.b62_encode(signing.b62_decode(real_ts(self)) - four_days))
        old = tokens.make_verify_token(unverified_client, unverified_client.email)
        monkeypatch.undo()
        assert _verify(old).status_code == 400
        unverified_client.client_profile.refresh_from_db()
        assert unverified_client.client_profile.email_verified is False

    def test_reset_token_is_not_a_verify_token(self, unverified_client):
        # Same signer, different salt: one kind of link can't stand in for another.
        assert _verify(tokens.make_reset_token(unverified_client)).status_code == 400

    def test_link_for_an_address_no_longer_on_the_account_is_dead(self, unverified_client):
        token = tokens.make_verify_token(unverified_client, unverified_client.email)
        unverified_client.email = 'changed@moonco.co.za'
        unverified_client.save()
        assert _verify(token).status_code == 400

    def test_inactive_account_link_rejected(self, unverified_client):
        token = tokens.make_verify_token(unverified_client, unverified_client.email)
        unverified_client.is_active = False
        unverified_client.save()
        assert _verify(token).status_code == 400

    def test_admin_created_client_signs_in_by_email_only_once_verified(self, unverified_client):
        # Not self-registered: the username always works…
        assert login('moonco').status_code == 200
        # …the email doesn't until it's confirmed (401, as if unknown).
        assert login('ops@moonco.co.za').status_code == 401
        token = tokens.make_verify_token(unverified_client, unverified_client.email)
        assert _verify(token).status_code == 200
        assert login('ops@moonco.co.za').status_code == 200


# ── resend verification ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestResendVerification:

    def _resend(self, email):
        return post(RESEND_URL, {'email': email})

    def test_reply_is_neutral(self, verified_client, unverified_client):
        replies = [self._resend(email) for email in
                   ('nobody@nowhere.example', verified_client.email, unverified_client.email)]
        assert {r.status_code for r in replies} == {200}
        assert len({r.data['detail'] for r in replies}) == 1

    def test_only_unverified_accounts_get_a_link(self, verified_client, unverified_client):
        self._resend('nobody@nowhere.example')
        self._resend(verified_client.email)
        self._resend(unverified_client.email.upper())
        assert [m.to for m in mail.outbox] == [[unverified_client.email]]
        assert mail.outbox[0].subject == VERIFY_SUBJECT
        assert _verify(token_in(mail.outbox[0])).status_code == 200

    def test_cooldown_limits_copies(self, unverified_client):
        for _ in range(3):
            assert self._resend(unverified_client.email).status_code == 200
        assert len(mail.outbox) == 1
        clear_verify_cooldown(unverified_client)
        self._resend(unverified_client.email)
        assert len(mail.outbox) == 2

    def test_pending_email_gets_its_link(self, verified_client):
        profile = verified_client.client_profile
        profile.pending_email = 'new@sunco.co.za'
        profile.save()
        self._resend('new@sunco.co.za')
        assert [m.to for m in mail.outbox] == [['new@sunco.co.za']]

    def test_inactive_account_gets_nothing(self, unverified_client):
        unverified_client.is_active = False
        unverified_client.save()
        assert self._resend(unverified_client.email).status_code == 200
        assert mail.outbox == []

    @pytest.mark.parametrize('body', [{}, {'email': 'nope'}, {'email': ['a@b.co']}])
    def test_bad_email_is_400(self, db, body):
        assert post(RESEND_URL, body).status_code == 400
