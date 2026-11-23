"""
Tests for the per-type switches (GET/PATCH /push/aps/preferences/) and the
"Receive admin alerts" account switch on the Staff accounts page (a per-account
setting, not a permission: off for everyone, superusers too; audited).
"""

import pytest
from django.contrib.auth.models import User

from apps.core.models import ClientProfile
from apps.push.models import NotificationPrefs
from apps.records.models import AuditLog
from tests.client_portal.conftest import api_for, make_client
from tests.gas_guard.conftest import auth_client_for

from .conftest import make_gg_user, make_staff

pytestmark = pytest.mark.django_db
PREFS = '/push/aps/preferences/'
STAFF = '/staff/accounts/'


@pytest.fixture
def boss(db):
    return User.objects.create_user('boss', password='x', is_staff=True, is_superuser=True)


def keys(res):
    return [t['key'] for t in res.data['types']]


# ── which types each person sees ────────────────────────────────────────────


def test_clients_see_only_ticket_messages():
    res = api_for(make_client('acme')).get(PREFS)
    assert res.status_code == 200
    assert res.data == {'types': [{'key': 'ticket_message', 'label': 'Ticket messages', 'on': True}],
                        'admin_alerts': False}


def test_staff_see_the_types_their_permissions_allow(boss):
    assert keys(api_for(boss).get(PREFS)) == ['ticket_message', 'job_assigned', 'visits_today', 'tool_overdue']
    jobs_only = make_staff('jobsonly', ['jobs.view'])
    assert keys(api_for(jobs_only).get(PREFS)) == ['job_assigned', 'visits_today']
    tools_chat = make_staff('toolschat', ['tools.view', 'tickets.chat_view'])
    assert keys(api_for(tools_chat).get(PREFS)) == ['ticket_message', 'job_assigned', 'visits_today', 'tool_overdue']
    # Every staff member can be on jobs, so the job types are always there.
    assert keys(api_for(make_staff('nothing', [])).get(PREFS)) == ['job_assigned', 'visits_today']


def test_sign_in_needed_and_gas_guard_has_none():
    assert api_for().get(PREFS).status_code == 401
    assert auth_client_for(make_gg_user('owner@example.com')).get(PREFS).status_code == 401
    assert api_for().get('/gas-guard/push/preferences/').status_code == 404


# ── switching types off and on ──────────────────────────────────────────────


def test_switch_off_and_on(boss):
    api = api_for(boss)
    res = api.patch(PREFS, {'visits_today': False, 'tool_overdue': False}, format='json')
    assert res.status_code == 200
    assert {t['key']: t['on'] for t in res.data['types']} == {
        'ticket_message': True, 'job_assigned': True, 'visits_today': False, 'tool_overdue': False}
    res = api.patch(PREFS, {'visits_today': True}, format='json')
    assert {t['key']: t['on'] for t in res.data['types']}['tool_overdue'] is False  # untouched
    assert NotificationPrefs.objects.get(user=boss).off_types == ['tool_overdue']


@pytest.mark.parametrize('body', [{}, {'nonsense': False}, {'visits_today': 'no'}, {'job_assigned': None}])
def test_bad_bodies_refused(boss, body):
    assert api_for(boss).patch(PREFS, body, format='json').status_code == 400


def test_cant_switch_a_type_you_cant_get():
    client = make_client('acme')
    assert api_for(client).patch(PREFS, {'visits_today': False}, format='json').status_code == 400
    assert not NotificationPrefs.objects.filter(user=client).exists()


def test_people_cant_give_themselves_admin_alerts(boss):
    api_for(boss).patch(PREFS, {'ticket_message': True, 'admin_alerts': True}, format='json')
    assert not NotificationPrefs.objects.filter(user=boss, admin_alerts=True).exists()


# ── "Receive admin alerts" on the Staff accounts page ───────────────────────


def test_off_by_default_for_everyone(boss):
    make_staff('thandi', ['jobs.view'])
    rows = api_for(boss).get(STAFF).data
    assert {r['username']: r['admin_alerts'] for r in rows} == {'boss': False, 'thandi': False}


def test_set_on_a_staff_account_and_audited(boss):
    thandi = make_staff('thandi', ['jobs.view'])
    api = api_for(boss)
    res = api.patch(f'{STAFF}{thandi.pk}/', {'admin_alerts': True}, format='json')
    assert res.status_code == 200 and res.data['admin_alerts'] is True
    assert NotificationPrefs.objects.get(user=thandi).admin_alerts is True
    log = AuditLog.objects.filter(action=AuditLog.UPDATED, object_id=str(thandi.pk)).latest('id')
    assert log.changes['admin_alerts'] == {'label': 'Receive admin alerts', 'old': 'No', 'new': 'Yes'}
    assert log.actor == boss
    # Their own per-type switches stay as they were.
    api.patch(f'{STAFF}{thandi.pk}/', {'admin_alerts': False}, format='json')
    assert NotificationPrefs.objects.get(user=thandi).admin_alerts is False


def test_superusers_can_get_it_too_but_nothing_else_changes(boss):
    other = User.objects.create_user('other_boss', password='x', is_superuser=True, first_name='Other')
    api = api_for(boss)
    res = api.patch(f'{STAFF}{other.pk}/', {'admin_alerts': True}, format='json')
    assert res.status_code == 200 and res.data['admin_alerts'] is True
    assert not ClientProfile.objects.filter(user=other).exists()  # account untouched
    assert AuditLog.objects.filter(object_id=str(other.pk), changes__admin_alerts__new='Yes').exists()
    # Other fields on a superuser are still refused.
    assert api.patch(f'{STAFF}{other.pk}/', {'name': 'Renamed'}, format='json').status_code == 400
    assert api.patch(f'{STAFF}{other.pk}/', {'admin_alerts': False, 'is_active': False},
                     format='json').status_code == 400
    # And their own.
    assert api.patch(f'{STAFF}{boss.pk}/', {'admin_alerts': True}, format='json').status_code == 200


def test_new_account_with_admin_alerts(boss):
    res = api_for(boss).post(STAFF, {'username': 'newbie', 'name': 'New Bie', 'password': 'Sunny-Days-2026!x',
                                     'confirm_password': 'Sunny-Days-2026!x', 'admin_alerts': True}, format='json')
    assert res.status_code == 201, res.data
    assert res.data['admin_alerts'] is True
    assert 'receives admin alerts' in AuditLog.objects.filter(action=AuditLog.CREATED).latest('id').summary


def test_only_staff_managers_set_it(boss):
    thandi = make_staff('thandi', ['jobs.view'])
    assert api_for(thandi).patch(f'{STAFF}{thandi.pk}/', {'admin_alerts': True}, format='json').status_code == 403
    assert not NotificationPrefs.objects.filter(user=thandi).exists()
