"""
Forgot / reset / change password and the subscriber "claim your account" flow
(apps.accounts).

Rules covered: forgot-password replies never reveal whether an email has an
account; a reset link works once, confirms the email and ends every existing
session (access AND refresh tokens); a subscriber with no login gets a link
that creates their account once; changing the password needs the current one
and hands back fresh tokens while ending every other session.
"""

import json
import threading

import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core import mail

from apps.accounts import tokens
from apps.accounts import views as account_views

from .conftest import (
    CHANGE_PASSWORD_URL,
    CLAIM_SUBJECT,
    FORGOT_URL,
    ME_URL,
    NEW_PASSWORD,
    PASSWORD,
    REFRESH_URL,
    RESET_SUBJECT,
    RESET_URL,
    anon,
    bearer,
    login,
    make_client,
    make_subscriber,
    post,
    token_in,
)


def _forgot(email):
    return post(FORGOT_URL, {'email': email})


def _reset(token, password=NEW_PASSWORD, confirm=None):
    return post(RESET_URL, {'token': token, 'password': password, 'confirm_password': confirm or password})


# ── forgot password ─────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestForgotPassword:

    def test_existing_account_gets_reset_link(self, verified_client):
        res = _forgot(' OPS@SunCo.co.za ')
        assert res.status_code == 200
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ['ops@sunco.co.za']
        assert mail.outbox[0].subject == RESET_SUBJECT
        assert '/dashboard/reset-password?token=' in mail.outbox[0].body
        # The username differs from the email, so the email reminds them of it.
        assert 'sunco' in mail.outbox[0].body

    def test_unverified_account_email_also_gets_reset_link(self, unverified_client):
        # Resetting proves the address, so it's allowed (and confirms it — see below).
        _forgot(unverified_client.email)
        assert [m.to for m in mail.outbox] == [[unverified_client.email]]

    def test_replies_never_reveal_accounts(self, verified_client):
        make_subscriber('payer@home.example')
        replies = [_forgot(e) for e in ('ops@sunco.co.za', 'nobody@nowhere.example', 'payer@home.example')]
        assert {r.status_code for r in replies} == {200}
        assert len({r.data['detail'] for r in replies}) == 1

    def test_unknown_email_gets_nothing(self, db):
        assert _forgot('nobody@nowhere.example').status_code == 200
        assert mail.outbox == []

    def test_inactive_account_gets_nothing(self, verified_client):
        verified_client.is_active = False
        verified_client.save()
        _forgot(verified_client.email)
        assert mail.outbox == []

    def test_subscriber_without_account_gets_claim_link(self, db):
        make_subscriber('payer@home.example')
        _forgot('Payer@Home.example')
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ['payer@home.example']
        assert mail.outbox[0].subject == CLAIM_SUBJECT

    def test_subscriber_with_account_gets_normal_reset(self, verified_client):
        make_subscriber(verified_client.email)
        _forgot(verified_client.email)
        assert [m.subject for m in mail.outbox] == [RESET_SUBJECT]

    def test_email_sent_off_the_request_thread(self, verified_client, monkeypatch):
        # Real (threaded) runner, with SMTP held up: the reply must come back
        # before the email goes, so response timing can't reveal the account.
        monkeypatch.setattr(account_views, '_run_in_background', account_views._run_in_background.real)
        release, sent = threading.Event(), threading.Event()

        def slow_send(user):
            release.wait(5)
            sent.set()

        monkeypatch.setattr(account_views, 'send_password_reset_email', slow_send)
        res = _forgot(verified_client.email)
        assert res.status_code == 200
        assert not sent.is_set()
        release.set()
        assert sent.wait(5)

    @pytest.mark.parametrize('body', [{}, {'email': 'nope'}, {'email': 5}, ['a@b.co']])
    def test_bad_body_is_400(self, db, body):
        res = anon().post(FORGOT_URL, json.dumps(body), content_type='application/json')
        assert res.status_code == 400
        assert mail.outbox == []


# ── reset password ──────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestResetPassword:

    def _link(self, user):
        _forgot(user.email)
        return token_in(mail.outbox[-1])

    def test_reset_sets_password_and_confirms_email(self, unverified_client):
        res = _reset(self._link(unverified_client))
        assert res.status_code == 200, res.data
        assert res.data['login'] == unverified_client.email
        unverified_client.refresh_from_db()
        assert unverified_client.check_password(NEW_PASSWORD)
        assert unverified_client.client_profile.email_verified is True
        assert login(unverified_client.email, NEW_PASSWORD).status_code == 200
        assert login('moonco', PASSWORD).status_code == 401

    def test_link_works_once(self, verified_client):
        token = self._link(verified_client)
        assert _reset(token).status_code == 200
        again = _reset(token, 'Third-Password-99')
        assert again.status_code == 400
        verified_client.refresh_from_db()
        assert verified_client.check_password(NEW_PASSWORD)

    def test_reset_ends_existing_sessions(self, verified_client):
        before = login('sunco').data
        assert bearer(before['access']).get(ME_URL).status_code == 200

        assert _reset(self._link(verified_client)).status_code == 200

        assert bearer(before['access']).get(ME_URL).status_code == 401
        refreshed = post(REFRESH_URL, {'refresh': before['refresh']})
        assert refreshed.status_code == 401
        assert 'access' not in refreshed.data

    def test_weak_password_rejected_and_link_still_usable(self, verified_client):
        token = self._link(verified_client)
        weak = _reset(token, 'password')
        assert weak.status_code == 400
        assert 'password' in weak.data
        mismatch = _reset(token, NEW_PASSWORD, 'Something-Else-1')
        assert mismatch.status_code == 400
        assert 'confirm_password' in mismatch.data
        assert _reset(token).status_code == 200

    def test_link_dies_when_email_changes(self, verified_client):
        token = self._link(verified_client)
        verified_client.email = 'moved@sunco.co.za'
        verified_client.save()
        assert _reset(token).status_code == 400

    def test_link_for_inactive_account_rejected(self, verified_client):
        token = self._link(verified_client)
        verified_client.is_active = False
        verified_client.save()
        assert _reset(token).status_code == 400

    @pytest.mark.parametrize('token', ['garbage', ''])
    def test_bad_token_rejected(self, db, token):
        assert _reset(token).status_code == 400

    def test_verify_token_is_not_a_reset_token(self, verified_client):
        assert _reset(tokens.make_verify_token(verified_client, verified_client.email)).status_code == 400

    def test_staff_can_reset_too(self, staff_user):
        staff_user.email = 'tech@aps.example'
        staff_user.save()
        res = _reset(self._link(staff_user))
        assert res.status_code == 200
        assert login('tech@aps.example', NEW_PASSWORD).status_code == 200


# ── claim (subscriber sets up an account) ───────────────────────────────────


@pytest.mark.django_db
class TestClaimAccount:

    def _claim_link(self, email='payer@home.example'):
        _forgot(email)
        return token_in(mail.outbox[-1])

    def test_claim_creates_verified_account(self):
        make_subscriber('payer@home.example', name='  Thandi Mokoena ', phone='082 000 1111')
        res = _reset(self._claim_link())
        assert res.status_code == 201, res.data
        assert res.data['login'] == 'payer@home.example'

        user = User.objects.get(email='payer@home.example')
        assert user.username == 'payer'
        assert user.check_password(NEW_PASSWORD)
        profile = user.client_profile
        assert profile.role == 'client'
        assert profile.email_verified is True
        assert profile.self_registered is True
        assert profile.company_name == 'Thandi Mokoena'
        assert profile.phone == '082 000 1111'

        signed_in = login('payer@home.example', NEW_PASSWORD)
        assert signed_in.status_code == 200
        subs = bearer(signed_in.data['access']).get('/client-portal/subscriptions/')
        assert subs.status_code == 200
        assert len(subs.data['subscriptions']) == 1

    def test_claim_link_works_once(self):
        make_subscriber('payer@home.example')
        token = self._claim_link()
        assert _reset(token).status_code == 201
        again = _reset(token, 'Hijack-Attempt-42')
        assert again.status_code == 400
        assert User.objects.filter(email='payer@home.example').count() == 1
        assert User.objects.get(email='payer@home.example').check_password(NEW_PASSWORD)

    def test_claim_refused_if_account_made_meanwhile(self):
        make_subscriber('payer@home.example')
        token = self._claim_link()
        make_client('someone', email='payer@home.example')
        assert _reset(token).status_code == 400
        assert User.objects.filter(email='payer@home.example').count() == 1

    def test_claim_refused_if_subscription_gone(self):
        sub = make_subscriber('payer@home.example')
        token = self._claim_link()
        sub.delete()
        assert _reset(token).status_code == 400
        assert not User.objects.filter(email='payer@home.example').exists()

    def test_claim_with_weak_password_creates_nothing(self):
        make_subscriber('payer@home.example')
        res = _reset(self._claim_link(), 'payer')
        assert res.status_code == 400
        assert 'password' in res.data
        assert not User.objects.filter(email='payer@home.example').exists()

    def test_nameless_subscriber_gets_local_part_as_name(self):
        make_subscriber('payer@home.example', name='', phone='')
        assert _reset(self._claim_link()).status_code == 201
        assert User.objects.get(email='payer@home.example').client_profile.company_name == 'payer'


# ── change password ─────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestChangePassword:

    def _change(self, api, current=PASSWORD, password=NEW_PASSWORD, confirm=None):
        return post(CHANGE_PASSWORD_URL, {'current_password': current, 'password': password,
                                          'confirm_password': confirm or password}, api=api)

    def test_requires_sign_in(self, db):
        assert post(CHANGE_PASSWORD_URL, {}).status_code == 401

    def test_wrong_current_password(self, verified_client):
        api = bearer(login('sunco').data['access'])
        res = self._change(api, current='Not-It-At-All-1')
        assert res.status_code == 400
        assert 'current_password' in res.data
        verified_client.refresh_from_db()
        assert verified_client.check_password(PASSWORD)

    @pytest.mark.parametrize('password,confirm,field', [
        (NEW_PASSWORD, 'Different-Pass-88', 'confirm_password'),
        (PASSWORD, None, 'password'),     # same as the current one
        ('password', None, 'password'),   # too common
        ('sunco', None, 'password'),      # too short / like the username
    ])
    def test_rejected_new_passwords(self, verified_client, password, confirm, field):
        api = bearer(login('sunco').data['access'])
        res = self._change(api, password=password, confirm=confirm)
        assert res.status_code == 400
        assert field in res.data

    def test_success_returns_fresh_tokens_and_ends_other_sessions(self, verified_client):
        this_session = login('sunco').data
        other_session = login('ops@sunco.co.za').data

        res = self._change(bearer(this_session['access']))
        assert res.status_code == 200, res.data
        assert res.data['access'] and res.data['refresh']

        assert bearer(res.data['access']).get(ME_URL).status_code == 200
        assert post(REFRESH_URL, {'refresh': res.data['refresh']}).status_code == 200
        for old in (this_session, other_session):
            assert bearer(old['access']).get(ME_URL).status_code == 401
            assert post(REFRESH_URL, {'refresh': old['refresh']}).status_code == 401

        assert login('sunco', NEW_PASSWORD).status_code == 200
        assert login('sunco', PASSWORD).status_code == 401
        assert authenticate(username='sunco', password=NEW_PASSWORD) is not None

    def test_throttled_per_account(self, verified_client):
        api = bearer(login('sunco').data['access'])
        codes = [self._change(api, current='Wrong-Guess-001').status_code for _ in range(11)]
        assert codes[:10] == [400] * 10
        assert codes[10] == 429
