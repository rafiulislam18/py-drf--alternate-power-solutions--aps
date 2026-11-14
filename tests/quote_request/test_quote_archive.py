"""
Archiving quote requests (all three kinds, by hand only): archive / unarchive
by kind + id, bulk by list key, quotes.archive, and the active / archived /
include-archived list with its counts.
"""

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.container_conversion.models import ServiceRequest
from apps.core.models import ClientProfile
from apps.quote_request.models import QuoteRequest
from apps.records.models import AuditLog
from apps.staff.models import StaffAccess

from .test_staff_quotes import LIST, container, data, quote  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


def api_as(username, perms=None, superuser=False):
    user = User.objects.create_user(username, is_superuser=superuser)
    ClientProfile.objects.create(user=user, role='admin')
    if perms is not None:
        StaffAccess.objects.create(user=user, permissions=perms)
    api = APIClient()
    api.force_authenticate(user)
    return api


@pytest.fixture
def boss():
    return api_as('boss', superuser=True)


def keys(res):
    return [r['key'] for r in res.data['results']]


@pytest.mark.parametrize('kind', ['general', 'solar', 'container'])
def test_every_kind_archives_and_unarchives(boss, data, kind):
    obj = {'general': data[0], 'solar': data[1], 'container': data[2]}[kind]
    res = boss.post(f'{LIST}{kind}/{obj.pk}/archive/')
    assert res.status_code == 200
    assert res.data['key'] == f'{kind}-{obj.pk}' and res.data['archive_reason'] == 'manual'
    obj.refresh_from_db()
    assert obj.is_archived
    assert boss.get(f'{LIST}{kind}/{obj.pk}/').data['archived_at']  # still viewable
    assert boss.post(f'{LIST}{kind}/{obj.pk}/unarchive/').data['archived_at'] is None
    entries = AuditLog.objects.filter(object_type=obj._meta.label_lower, object_id=str(obj.pk))
    assert sorted(entries.values_list('action', 'section')) == [('archived', 'quotes'), ('unarchived', 'quotes')]


def test_the_kind_must_match(boss, data):
    general, solar, _ = data
    assert boss.post(f'{LIST}solar/{general.pk}/archive/').status_code == 404
    assert boss.post(f'{LIST}general/{solar.pk}/archive/').status_code == 404
    assert boss.post(f'{LIST}nope/{general.pk}/archive/').status_code == 404


def test_archive_needs_the_permission(data):
    viewer = api_as('viewer', ['quotes.view'])
    general = data[0]
    assert viewer.post(f'{LIST}general/{general.pk}/archive/').status_code == 403
    assert viewer.post(f'{LIST}bulk-archive/', {'ids': [f'general-{general.pk}'], 'archived': True},
                       format='json').status_code == 403
    archiver = api_as('archiver', ['quotes.view', 'quotes.archive'])
    assert archiver.post(f'{LIST}general/{general.pk}/archive/').status_code == 200


def test_bulk_archive_by_list_key(boss, data):
    g, s, c = data
    res = boss.post(f'{LIST}bulk-archive/', {
        'ids': [f'general-{g.pk}', f'container-{c.pk}', f'solar-{g.pk}', 'general-999999', 'junk', 5],
        'archived': True,
    }, format='json')
    assert res.data == {'done': [f'general-{g.pk}', f'container-{c.pk}'],
                        'skipped': [f'solar-{g.pk}', 'general-999999']}
    assert QuoteRequest.objects.get(pk=g.pk).is_archived and ServiceRequest.objects.get(pk=c.pk).is_archived
    assert not QuoteRequest.objects.get(pk=s.pk).is_archived


def test_lists_and_counts_follow_the_view(boss, data):
    g, s, c = data
    boss.post(f'{LIST}general/{g.pk}/archive/')
    boss.post(f'{LIST}container/{c.pk}/archive/')

    active = boss.get(LIST)
    assert keys(active) == [f'solar-{s.pk}']
    assert active.data['counts'] == {'general': 0, 'solar': 1, 'container': 0, 'all': 1, 'new': 0}
    assert active.data['archived_count'] == 2

    archived = boss.get(LIST, {'archived': 1})
    assert keys(archived) == [f'container-{c.pk}', f'general-{g.pk}']
    assert archived.data['counts'] == {'general': 1, 'solar': 0, 'container': 1, 'all': 2, 'new': 2}
    assert boss.get(LIST, {'archived': 1, 'kind': 'container'}).data['count'] == 1

    assert keys(boss.get(LIST, {'q': 'gina'})) == []
    found = boss.get(LIST, {'q': 'gina', 'include_archived': 1}).data['results']
    assert [(r['key'], bool(r['archived_at'])) for r in found] == [(f'general-{g.pk}', True)]
    assert found[0]['archived_by']['name'] == 'boss'


def test_new_quotes_arrive_active(boss):
    quote('Lindiwe')
    container('Musa')
    res = boss.get(LIST)
    assert res.data['counts']['all'] == 2 and res.data['archived_count'] == 0
