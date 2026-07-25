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

    def test_create_with_site_id_links_to_site(self, admin_api_client, client_user):
        s = _site(client_user, name='Bantry Bay', has_battery=True)
        payload = _report_payload(client_user.id, [
            {'site_id': s.id, 'solar_yield': '120.00', 'estimated_saving': '40.00'},
        ])
        res = admin_api_client.post('/dashboard/reports/', payload, format='json')
        assert res.status_code == 201, res.data
        row = SiteData.objects.get()
        assert row.site_id == s.id
        assert str(row.solar_yield) == '120.00'
        # Identity comes from the linked Site.
        assert row.site.name == 'Bantry Bay'
        assert row.site.has_battery is True

    def test_row_without_site_id_rejected(self, admin_api_client, client_user):
        payload = _report_payload(client_user.id, [
            {'solar_yield': '10.00'},   # no site_id
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
        SiteData.objects.create(report=report, site=s, solar_yield='99.00')
        res = admin_api_client.get(f'/dashboard/reports/{report.uuid}/')
        assert res.status_code == 200
        site_row = res.data['sites'][0]
        assert site_row['site']['id'] == s.id
        assert site_row['site']['name'] == 'Alpha'
        assert site_row['site']['has_battery'] is True

    def test_exact_read_reflects_site_rename(self, admin_api_client, client_user):
        """The report's site_name/has_battery in the read come from the linked Site,
        so a rename/battery-toggle shows through immediately."""
        s = _site(client_user, name='Original', has_battery=False)
        report = SolarReport.objects.create(
            client=client_user, report_date='2026-07-01',
            period_start='2026-07-01', period_end='2026-07-31')
        SiteData.objects.create(report=report, site=s, solar_yield='5.00')
        # Rename + battery-toggle the Site record.
        s.name = 'Renamed'
        s.has_battery = True
        s.save()

        res = admin_api_client.get(f'/dashboard/reports/{report.uuid}/')
        row = res.data['sites'][0]
        assert row['site_name'] == 'Renamed'    # sourced from the Site
        assert row['has_battery'] is True

    def test_read_pads_active_sites_with_zero_rows(self, admin_api_client, client_user):
        """Exact read lists all active sites: real rows in_report=True, padded zeros
        in_report=False."""
        s_in = _site(client_user, name='HasData')
        _site(client_user, name='NoData1')
        _site(client_user, name='NoData2')
        report = SolarReport.objects.create(
            client=client_user, report_date='2026-07-01',
            period_start='2026-07-01', period_end='2026-07-31')
        SiteData.objects.create(report=report, site=s_in, solar_yield='42.00')

        res = admin_api_client.get(f'/dashboard/reports/{report.uuid}/')
        rows = {r['site_name']: r for r in res.data['sites']}
        assert set(rows) == {'HasData', 'NoData1', 'NoData2'}
        assert rows['HasData']['in_report'] is True
        assert rows['HasData']['solar_yield'] == '42.00'
        assert rows['NoData1']['in_report'] is False
        assert rows['NoData1']['solar_yield'] == '0.00'

    def test_read_includes_inactive_site_with_data(self, admin_api_client, client_user):
        """A retired site that has data in this report still appears (in_report=True)."""
        retired = _site(client_user, name='Retired')
        retired.is_active = False
        retired.save()
        report = SolarReport.objects.create(
            client=client_user, report_date='2026-07-01',
            period_start='2026-07-01', period_end='2026-07-31')
        SiteData.objects.create(report=report, site=retired, solar_yield='7.00')

        res = admin_api_client.get(f'/dashboard/reports/{report.uuid}/')
        rows = {r['site_name']: r for r in res.data['sites']}
        assert 'Retired' in rows
        assert rows['Retired']['in_report'] is True

    def test_read_excludes_inactive_site_without_data(self, admin_api_client, client_user):
        active = _site(client_user, name='Active')
        ghost = _site(client_user, name='Ghost')
        ghost.is_active = False
        ghost.save()
        report = SolarReport.objects.create(
            client=client_user, report_date='2026-07-01',
            period_start='2026-07-01', period_end='2026-07-31')
        SiteData.objects.create(report=report, site=active, solar_yield='1.00')

        res = admin_api_client.get(f'/dashboard/reports/{report.uuid}/')
        names = {r['site_name'] for r in res.data['sites']}
        assert 'Ghost' not in names        # retired + no data -> hidden
        assert 'Active' in names

    def test_update_replaces_site_rows(self, admin_api_client, client_user):
        s1 = _site(client_user, name='One')
        s2 = _site(client_user, name='Two')
        report = SolarReport.objects.create(
            client=client_user, report_date='2026-07-01',
            period_start='2026-07-01', period_end='2026-07-31')
        SiteData.objects.create(report=report, site=s1, solar_yield='1.00')

        payload = _report_payload(client_user.id, [
            {'site_id': s2.id, 'solar_yield': '2.00'},
        ])
        res = admin_api_client.put(f'/dashboard/reports/{report.uuid}/',
                                   payload, format='json')
        assert res.status_code == 200, res.data
        rows = SiteData.objects.filter(report=report)
        assert rows.count() == 1
        assert rows.first().site_id == s2.id
