"""
Archiving solar estimates (by hand only): archive / unarchive / bulk with
estimates.archive, the active / archived / include-archived list with its
per-status counts, and trash + restore keeping the archive.
"""

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.core.models import ClientProfile
from apps.records.models import AuditLog
from apps.solar_estimates.models import SolarEstimate
from apps.staff.models import StaffAccess

from .conftest import inputs

pytestmark = pytest.mark.django_db
BASE = '/solar-estimates/'


def new(api, **over):
    res = api.post(BASE, {'inputs': inputs(**over)}, format='json')
    assert res.status_code == 201, res.data
    return res.data['id']


def ids(res):
    return [r['id'] for r in res.data['results']]


def test_archive_and_unarchive(staff, catalogue):
    pk = new(staff)
    res = staff.post(f'{BASE}{pk}/archive/')
    assert res.status_code == 200 and res.data['archive_reason'] == 'manual'
    assert staff.get(f'{BASE}{pk}/').data['archived_at']  # still opens
    assert staff.post(f'{BASE}{pk}/unarchive/').data['archived_at'] is None
    actions = AuditLog.objects.filter(object_type='solar_estimates.solarestimate', object_id=str(pk))
    assert sorted(actions.values_list('action', flat=True)) == ['archived', 'created', 'unarchived']


def test_confirmed_proposals_can_be_archived(staff, catalogue):
    pk = new(staff)
    staff.post(f'{BASE}{pk}/confirm/', {}, format='json')
    assert staff.post(f'{BASE}{pk}/archive/').status_code == 200


def api_with(username, perms):
    user = User.objects.create_user(username)
    ClientProfile.objects.create(user=user, role='admin')
    StaffAccess.objects.create(user=user, permissions=perms)
    api = APIClient()
    api.force_authenticate(user)
    return api


def test_archive_needs_the_permission(staff, catalogue):
    pk = new(staff)
    editor = api_with('editor', ['estimates.view', 'estimates.edit'])
    assert editor.post(f'{BASE}{pk}/archive/').status_code == 403
    assert editor.post(f'{BASE}bulk-archive/', {'ids': [pk], 'archived': True}, format='json').status_code == 403
    archiver = api_with('archiver', ['estimates.view', 'estimates.archive'])
    assert archiver.post(f'{BASE}{pk}/archive/').status_code == 200


def test_bulk_archive(staff, catalogue):
    a, b = new(staff), new(staff)
    res = staff.post(f'{BASE}bulk-archive/', {'ids': [a, b, 999999], 'archived': True}, format='json')
    assert res.data == {'done': [a, b], 'skipped': [999999]}
    assert SolarEstimate.objects.filter(archived_at__isnull=False).count() == 2


def test_lists_and_counts_follow_the_view(staff, catalogue):
    draft = new(staff, client_name='Bel Ombre Trust')
    confirmed = new(staff, client_name='Seaview Estates')
    staff.post(f'{BASE}{confirmed}/confirm/', {}, format='json')
    old = new(staff, client_name='Old Seaview Flats')
    staff.post(f'{BASE}{old}/archive/')

    active = staff.get(BASE)
    assert set(ids(active)) == {draft, confirmed}
    assert active.data['counts'] == {'draft': 1, 'issued': 1, 'all': 2} and active.data['archived_count'] == 1
    assert ids(staff.get(BASE, {'status': 'issued'})) == [confirmed]
    assert staff.get(BASE, {'status': 'issued'}).data['counts']['all'] == 2  # counts ignore the status tab

    archived = staff.get(BASE, {'archived': 1})
    assert ids(archived) == [old] and archived.data['counts'] == {'draft': 1, 'issued': 0, 'all': 1}
    assert archived.data['results'][0]['archived_by']['name'] == 'ops'

    assert set(ids(staff.get(BASE, {'q': 'seaview'}))) == {confirmed}
    assert set(ids(staff.get(BASE, {'q': 'seaview', 'include_archived': 1}))) == {confirmed, old}


def test_trash_and_restore_keep_the_archive(staff, catalogue):
    pk = new(staff)
    staff.post(f'{BASE}{pk}/archive/')
    assert staff.delete(f'{BASE}{pk}/').status_code == 204
    assert staff.get(BASE, {'archived': 1}).data['count'] == 0  # trashed: in no list
    assert staff.post(f'/records/trash/solar_estimates.solarestimate/{pk}/restore/').status_code == 200
    assert ids(staff.get(BASE, {'archived': 1})) == [pk]
