"""
Tests for the Site model + CRUD API (apps.solar_dashboard).

Sites are reusable per-client identity records (name + battery). Admin-only to
manage; unique per client; soft-retire (is_active=False) and a hard-delete guard
when report history exists.
"""

import pytest

from django.contrib.auth.models import User

from apps.core.models import ClientProfile
from apps.solar_dashboard.models import SolarReport, SiteData, Site


@pytest.fixture
def client_user(db, user_factory):
    """A client (has ClientProfile role=client) — treated as non-admin."""
    u = user_factory(username='acme')
    ClientProfile.objects.create(user=u, role='client', company_name='Acme')
    return u


def _site(client, name='Bantry Bay', **kw):
    return Site.objects.create(client=client, name=name, **kw)


@pytest.mark.django_db
class TestSiteModel:

    def test_unique_per_client(self, client_user):
        from django.db import IntegrityError, transaction
        _site(client_user, name='Sea Point')
        with pytest.raises(IntegrityError):
            with transaction.atomic():   # keep the outer test transaction usable
                _site(client_user, name='Sea Point')

    def test_same_name_different_clients_ok(self, client_user, user_factory):
        other = user_factory(username='other')
        ClientProfile.objects.create(user=other, role='client', company_name='Other')
        _site(client_user, name='Shared')
        _site(other, name='Shared')  # no error
        assert Site.objects.filter(name='Shared').count() == 2


@pytest.mark.django_db
class TestSiteAPI:

    def test_list_requires_admin(self, api_client, client_user):
        api_client.force_authenticate(user=client_user)
        assert api_client.get('/dashboard/sites/').status_code == 403

    def test_list_requires_auth(self, api_client):
        assert api_client.get('/dashboard/sites/').status_code == 401

    def test_admin_lists_by_client_active_only(self, admin_api_client, client_user):
        _site(client_user, name='Active1')
        _site(client_user, name='Retired1', is_active=False)
        res = admin_api_client.get(f'/dashboard/sites/?client_id={client_user.id}')
        assert res.status_code == 200
        names = {s['name'] for s in res.data}
        assert names == {'Active1'}  # inactive hidden by default

    def test_include_inactive(self, admin_api_client, client_user):
        _site(client_user, name='Active1')
        _site(client_user, name='Retired1', is_active=False)
        res = admin_api_client.get(
            f'/dashboard/sites/?client_id={client_user.id}&include_inactive=1')
        names = {s['name'] for s in res.data}
        assert names == {'Active1', 'Retired1'}

    def test_create_site(self, admin_api_client, client_user):
        res = admin_api_client.post('/dashboard/sites/', {
            'client_id': client_user.id, 'name': 'New Site', 'has_battery': True,
        }, format='json')
        assert res.status_code == 201
        assert res.data['name'] == 'New Site'
        assert res.data['has_battery'] is True
        assert Site.objects.filter(client=client_user, name='New Site').count() == 1

    def test_create_duplicate_name_rejected(self, admin_api_client, client_user):
        _site(client_user, name='Dup')
        res = admin_api_client.post('/dashboard/sites/', {
            'client_id': client_user.id, 'name': 'Dup',
        }, format='json')
        assert res.status_code == 400
        # Exact-name dup is caught by DRF's UniqueTogetherValidator (non_field_errors);
        # our custom validator adds the case-insensitive guard (test below).
        assert 'name' in res.data or 'non_field_errors' in res.data

    def test_create_duplicate_case_insensitive(self, admin_api_client, client_user):
        _site(client_user, name='Dup')
        res = admin_api_client.post('/dashboard/sites/', {
            'client_id': client_user.id, 'name': 'DUP',
        }, format='json')
        assert res.status_code == 400

    def test_create_forbidden_for_client(self, api_client, client_user):
        api_client.force_authenticate(user=client_user)
        res = api_client.post('/dashboard/sites/', {
            'client_id': client_user.id, 'name': 'Nope',
        }, format='json')
        assert res.status_code == 403

    def test_patch_rename(self, admin_api_client, client_user):
        s = _site(client_user, name='Old')
        res = admin_api_client.patch(f'/dashboard/sites/{s.id}/',
                                     {'name': 'Renamed'}, format='json')
        assert res.status_code == 200
        s.refresh_from_db()
        assert s.name == 'Renamed'

    def test_patch_toggle_active(self, admin_api_client, client_user):
        s = _site(client_user, name='S')
        res = admin_api_client.patch(f'/dashboard/sites/{s.id}/',
                                     {'is_active': False}, format='json')
        assert res.status_code == 200
        s.refresh_from_db()
        assert s.is_active is False

    def test_delete_without_history_hard_deletes(self, admin_api_client, client_user):
        s = _site(client_user, name='Fresh')
        res = admin_api_client.delete(f'/dashboard/sites/{s.id}/')
        assert res.status_code == 204
        assert not Site.objects.filter(pk=s.id).exists()

    def test_delete_with_history_soft_retires(self, admin_api_client, client_user):
        s = _site(client_user, name='Used')
        report = SolarReport.objects.create(
            client=client_user, report_date='2026-07-01',
            period_start='2026-07-01', period_end='2026-07-31',
        )
        SiteData.objects.create(report=report, site=s)
        res = admin_api_client.delete(f'/dashboard/sites/{s.id}/')
        assert res.status_code == 200
        assert res.data.get('retired') is True
        s.refresh_from_db()
        assert s.is_active is False          # retired, not deleted
        assert Site.objects.filter(pk=s.id).exists()

    def test_data_row_count_reported(self, admin_api_client, client_user):
        s = _site(client_user, name='Counted')
        report = SolarReport.objects.create(
            client=client_user, report_date='2026-07-01',
            period_start='2026-07-01', period_end='2026-07-31',
        )
        SiteData.objects.create(report=report, site=s)
        res = admin_api_client.get(
            f'/dashboard/sites/?client_id={client_user.id}')
        assert res.data[0]['data_row_count'] == 1
