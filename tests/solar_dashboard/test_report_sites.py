"""
Tests for report create/edit with the new site_id linkage (Step 4).

The report serializer now accepts each site row EITHER by site_id (new) OR by
site_name (old, backward-compatible). When a site_id is given, the legacy
site_name/has_battery columns are snapshotted from the Site, and the Site must
belong to the report's client.
"""

import pytest

from apps.core.models import ClientProfile
from apps.solar_dashboard.models import SolarReport, SiteData, Site


@pytest.fixture
def client_user(db, user_factory):
    u = user_factory(username='sunco')
    ClientProfile.objects.create(user=u, role='client', company_name='SunCo')
    return u


def _site(client, name='Alpha', has_battery=False):
    return Site.objects.create(client=client, name=name, has_battery=has_battery)


def _report_payload(client_id, sites):
    return {
        'client_id': client_id,
        'report_date': '2026-07-01',
        'period_start': '2026-07-01',
        'period_end': '2026-07-31',
        'sites': sites,
    }


@pytest.mark.django_db
class TestReportWithSiteId:

    def test_create_with_site_id_snapshots_identity(self, admin_api_client, client_user):
        s = _site(client_user, name='Bantry Bay', has_battery=True)
        payload = _report_payload(client_user.id, [
            {'site_id': s.id, 'solar_yield': '120.00', 'estimated_saving': '40.00'},
        ])
        res = admin_api_client.post('/dashboard/reports/', payload, format='json')
        assert res.status_code == 201, res.data
        row = SiteData.objects.get()
        assert row.site_id == s.id
        # Legacy columns snapshotted from the Site.
        assert row.site_name == 'Bantry Bay'
        assert row.has_battery is True
        assert str(row.solar_yield) == '120.00'

    def test_create_with_legacy_site_name_still_works(self, admin_api_client, client_user):
        payload = _report_payload(client_user.id, [
            {'site_name': 'Legacy Site', 'has_battery': False, 'solar_yield': '10.00'},
        ])
        res = admin_api_client.post('/dashboard/reports/', payload, format='json')
        assert res.status_code == 201, res.data
        row = SiteData.objects.get()
        assert row.site_id is None
        assert row.site_name == 'Legacy Site'

    def test_row_without_site_or_name_rejected(self, admin_api_client, client_user):
        payload = _report_payload(client_user.id, [
            {'solar_yield': '10.00'},   # no site_id, no site_name
        ])
        res = admin_api_client.post('/dashboard/reports/', payload, format='json')
        assert res.status_code == 400

    def test_site_from_other_client_rejected(self, admin_api_client, client_user, user_factory):
        other = user_factory(username='other')
        ClientProfile.objects.create(user=other, role='client', company_name='Other')
        foreign = _site(other, name='Foreign')
        payload = _report_payload(client_user.id, [
            {'site_id': foreign.id, 'solar_yield': '5.00'},
        ])
        res = admin_api_client.post('/dashboard/reports/', payload, format='json')
        assert res.status_code == 400
        assert not SolarReport.objects.exists()   # nothing persisted

    def test_read_report_exposes_nested_site(self, admin_api_client, client_user):
        s = _site(client_user, name='Alpha', has_battery=True)
        report = SolarReport.objects.create(
            client=client_user, report_date='2026-07-01',
            period_start='2026-07-01', period_end='2026-07-31')
        SiteData.objects.create(report=report, site=s, site_name='Alpha',
                                has_battery=True, solar_yield='99.00')
        res = admin_api_client.get(f'/dashboard/reports/{report.uuid}/')
        assert res.status_code == 200
        site_row = res.data['sites'][0]
        assert site_row['site']['id'] == s.id
        assert site_row['site']['name'] == 'Alpha'
        assert site_row['site']['has_battery'] is True

    def test_update_replaces_site_rows(self, admin_api_client, client_user):
        s1 = _site(client_user, name='One')
        s2 = _site(client_user, name='Two')
        report = SolarReport.objects.create(
            client=client_user, report_date='2026-07-01',
            period_start='2026-07-01', period_end='2026-07-31')
        SiteData.objects.create(report=report, site=s1, site_name='One',
                                solar_yield='1.00')

        payload = _report_payload(client_user.id, [
            {'site_id': s2.id, 'solar_yield': '2.00'},
        ])
        res = admin_api_client.put(f'/dashboard/reports/{report.uuid}/',
                                   payload, format='json')
        assert res.status_code == 200, res.data
        rows = SiteData.objects.filter(report=report)
        assert rows.count() == 1
        assert rows.first().site_id == s2.id
