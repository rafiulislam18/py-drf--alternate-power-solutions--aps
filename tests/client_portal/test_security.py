"""
Hardening tests for the client dashboard API (apps.client_portal).

Covers: the dashboard token rules as the client pages see them (a password
change ends other sessions, tokens without the password version are refused,
a Manage-Subscriptions portal token can't be replayed here), a profile-less
non-staff login being treated as a client (never staff), malformed input
answering 400 / empty results instead of 500, and ticket titles with newlines
staying out of email headers.
"""

import json

import pytest
from django.contrib.auth.models import User
from django.core import mail
from rest_framework_simplejwt.tokens import RefreshToken

from apps.client_portal.emails import notify_team_new_ticket
from apps.client_portal.models import Ticket
from apps.solar_dashboard.models import Site

from .conftest import PASSWORD, api_for, bearer, login, make_service, make_ticket

ME_URL = '/client-portal/me/'
TICKETS_URL = '/client-portal/tickets/'
SITES_URL = '/client-portal/sites/'
CANCEL_URL = '/client-portal/subscriptions/cancel/'
CHANGE_PASSWORD_URL = '/accounts/password/change/'


# ── sessions ────────────────────────────────────────────────────────────────


def test_password_change_ends_other_sessions(client_user):
    phone = bearer(login('acme').data['access'])
    laptop_tokens = login('acme').data
    laptop = bearer(laptop_tokens['access'])
    assert phone.get(ME_URL).status_code == 200

    res = laptop.post(CHANGE_PASSWORD_URL, {'current_password': PASSWORD, 'password': 'Brand-New-Pass-77',
                                            'confirm_password': 'Brand-New-Pass-77'}, format='json')
    assert res.status_code == 200, res.data
    assert phone.get(ME_URL).status_code == 401
    assert laptop.get(ME_URL).status_code == 401  # the old pair is dead too…
    assert bearer(res.data['access']).get(ME_URL).status_code == 200  # …the fresh one works


def test_password_changed_in_admin_ends_sessions(client_user):
    api = bearer(login('acme').data['access'])
    client_user.set_password('Set-By-APS-2026')
    client_user.save()
    assert api.get(ME_URL).status_code == 401


def _token(user, **claims):
    refresh = RefreshToken.for_user(user)
    for key, value in claims.items():
        refresh[key] = value
    return str(refresh.access_token)


@pytest.mark.parametrize('pv', [None, '', 'deadbeefdeadbeef', 0, True])
def test_token_without_matching_password_version_rejected(client_user, pv):
    claims = {} if pv is None else {'pv': pv}
    assert bearer(_token(client_user, **claims)).get(ME_URL).status_code == 401


def test_token_with_current_password_version_accepted(client_user):
    from apps.accounts.tokens import password_version

    assert bearer(_token(client_user, pv=password_version(client_user))).get(ME_URL).status_code == 200


def test_public_portal_token_cannot_be_replayed(client_user):
    # The Manage-Subscriptions portal signs its own JWTs with the same key.
    from apps.subscription_portal.tokens import issue_portal_token

    api = bearer(issue_portal_token(client_user.email))
    assert api.get(ME_URL).status_code == 401
    assert api.get('/client-portal/subscriptions/').status_code == 401


# ── roles ───────────────────────────────────────────────────────────────────


def test_profileless_non_staff_user_is_a_client(db):
    # e.g. an account made in the Django admin without "staff" ticked.
    user = User.objects.create_user(username='bare', email='bare@example.com', password=PASSWORD)
    api = api_for(user)
    res = api.get(ME_URL)
    assert res.status_code == 200
    assert res.data['company']['name'] == 'bare'
    assert res.data['email_verified'] is False
    # …and gets nothing staff-only.
    assert api.get('/dashboard/clients/').status_code == 403
    # It can add a site and raise a ticket like any client.
    site_id = api.post(SITES_URL, {'name': 'Home'}, format='json').data['id']
    res = api.post(TICKETS_URL, {'service': make_service('Solar').pk, 'title': 'Panels dirty', 'description': 'x',
                                 'site': site_id}, format='multipart')
    assert res.status_code == 201
    assert Ticket.objects.get().client == user


def test_profileless_staff_user_is_not_a_client(staff_user):
    api = api_for(staff_user)
    assert api.get(ME_URL).status_code == 403
    assert api.get('/dashboard/clients/').status_code == 200


# ── malformed input ─────────────────────────────────────────────────────────


@pytest.mark.parametrize('params', [
    {'site': '²'},
    {'site': '9' * 40},
    {'site': '-1'},
    {'page_size': 'x'},
    {'ordering': '--title'},
    {'search': '²'},
    {'search': 'APS-' + '9' * 40},
    {'search': '¹²³⁴'},
    {'search': 'leak\x00'},
    {'search': '%'},
])
def test_ticket_list_survives_odd_params(client_user, api, site, params):
    make_ticket(client_user, site, title='Roof leak', service='solar')
    res = api.get(TICKETS_URL, params)
    assert res.status_code == 200


def test_pk_from_reference_rejects_unicode_and_huge_digits():
    assert Ticket.pk_from_reference('²') is None
    assert Ticket.pk_from_reference('APS-¹²³⁴') is None
    assert Ticket.pk_from_reference('9' * 40) is None
    assert Ticket.pk_from_reference('') is None
    assert Ticket.pk_from_reference(None) is None
    assert Ticket.pk_from_reference('APS-1000') is None  # offset → pk 0


@pytest.mark.parametrize('body', [{'ref': 5}, {'ref': ['x']}, {'ref': None}, {}, ['x'], 'x'])
def test_cancel_rejects_malformed_ref(api, body):
    res = api.post(CANCEL_URL, json.dumps(body), content_type='application/json')
    assert res.status_code == 400


@pytest.mark.parametrize('body', [['Block C'], 'Block C', {'name': ['Block C']}, {'name': {'a': 'Block C'}}])
def test_site_create_rejects_malformed_body(api, body):
    res = api.post(SITES_URL, json.dumps(body), content_type='application/json')
    assert res.status_code == 400
    assert not Site.objects.filter(name='Block C').exists()


def test_ticket_create_rejects_json_array(api, site):
    res = api.post(TICKETS_URL, json.dumps([{'site': site.pk}]), content_type='application/json')
    assert res.status_code == 400
    assert not Ticket.objects.exists()


@pytest.mark.parametrize('site_value', ['²', '9' * 40, 'abc', '-1'])
def test_ticket_create_odd_site_is_a_field_error(api, site, site_value):
    res = api.post(TICKETS_URL, {'service': make_service('Solar').pk, 'title': 't', 'description': 'd', 'site': site_value},
                   format='multipart')
    assert res.status_code == 400
    assert 'site' in res.data


# ── ticket titles in email subjects ─────────────────────────────────────────


def test_title_newline_is_flattened_and_team_still_emailed(api, site, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        res = api.post(
            TICKETS_URL,
            {
                'service': make_service('Electrical').pk,
                'title': 'DB board\r\ntripping\n in block B',
                'description': 'Trips when the aircon starts.',
                'site': site.pk,
            },
            format='multipart',
        )
    assert res.status_code == 201, res.data
    assert Ticket.objects.get().title == 'DB board tripping in block B'
    team = [m for m in mail.outbox if 'New ticket' in m.subject]
    assert len(team) == 1
    assert 'DB board tripping in block B' in team[0].subject


def test_email_subject_strips_newlines_defensively(client_user, site):
    # e.g. a title edited in the admin, which doesn't go through the serializer.
    ticket = make_ticket(client_user, site, title='Line one\nLine two', service='solar')
    assert notify_team_new_ticket(ticket) is True
    assert '\n' not in mail.outbox[0].subject
    assert 'Line one Line two' in mail.outbox[0].subject


def test_company_name_with_newline_stays_out_of_subject(client_user, site):
    client_user.client_profile.company_name = 'Acme\r\nBcc: evil@example.com'
    client_user.client_profile.save()
    ticket = make_ticket(User.objects.get(pk=client_user.pk), site)
    assert notify_team_new_ticket(ticket) is True
    subject = mail.outbox[0].subject
    assert '\n' not in subject and '\r' not in subject
    assert mail.outbox[0].to == ['admin@example.com']
    assert mail.outbox[0].bcc == []
