"""
Tests for the generalized date-range aggregate dashboard
(apps.solar_dashboard.SolarReportAggregateView).

The view now works for ANY client using their real Site records:
- site list = active sites UNION any site (active/retired) with data in range
- grouped by site_id (rename-safe)
- active-empty site -> all-zero row; retired + no data in range -> hidden
- sum with pro-rata clipping + daily-avg extrapolation (math unchanged)

Public endpoint (shared dashboard links), so no auth needed for GET.
"""

from decimal import Decimal

import pytest

from apps.core.models import ClientProfile
from apps.solar_dashboard.models import SolarReport, SiteData, Site


@pytest.fixture
def client_user(db, user_factory):
    u = user_factory(username='sunco')
    ClientProfile.objects.create(user=u, role='client', company_name='SunCo')
    return u


def _site(client, name, has_battery=False, is_active=True, order=0):
    return Site.objects.create(client=client, name=name, has_battery=has_battery,
                               is_active=is_active, order=order)


def _report(client, start, end):
    return SolarReport.objects.create(
        client=client, report_date=start, period_start=start, period_end=end)


def _row(report, site, **metrics):
    return SiteData.objects.create(report=report, site=site, **metrics)


def _agg(api_client, report, frm, to):
    return api_client.get(
        f'/dashboard/reports/aggregate/?report_uuid={report.uuid}&from={frm}&to={to}')


@pytest.mark.django_db
class TestAggregate:

    def test_works_for_any_client(self, api_client, client_user):
        """Previously gated to 'Urban Growth' — now any client works."""
        s = _site(client_user, 'Alpha')
        r = _report(client_user, '2026-06-01', '2026-06-30')
        _row(r, s, solar_yield=Decimal('300'), estimated_saving=Decimal('90'))
        res = _agg(api_client, r, '2026-06-01', '2026-06-30')
        assert res.status_code == 200
        names = [x['site_name'] for x in res.data['sites']]
        assert 'Alpha' in names

    def test_full_range_exact_totals(self, api_client, client_user):
        """Range == report period, so no extrapolation: totals are exact."""
        s = _site(client_user, 'Alpha')
        r = _report(client_user, '2026-06-01', '2026-06-30')
        _row(r, s, solar_yield=Decimal('300'), estimated_saving=Decimal('90'))
        res = _agg(api_client, r, '2026-06-01', '2026-06-30')
        site = next(x for x in res.data['sites'] if x['site_name'] == 'Alpha')
        assert site['solar_yield'] == '300.00'
        assert site['estimated_saving'] == '90.00'
        assert res.data['uncovered_days'] == 0

    def test_groups_by_site_id_rename_safe(self, api_client, client_user):
        """Two reports for the same site sum together even if display name changed."""
        s = _site(client_user, 'Alpha')
        r1 = _report(client_user, '2026-06-01', '2026-06-30')
        r2 = _report(client_user, '2026-07-01', '2026-07-31')
        _row(r1, s, solar_yield=Decimal('100'))
        _row(r2, s, solar_yield=Decimal('50'))
        res = _agg(api_client, r1, '2026-06-01', '2026-07-31')
        rows = [x for x in res.data['sites'] if x['site_name'] == 'Alpha']
        assert len(rows) == 1                    # one combined row, not two
        assert rows[0]['solar_yield'] == '150.00'

    def test_active_empty_site_shows_zero_row(self, api_client, client_user):
        """An active site with no data in range still appears (all-zero)."""
        s_data = _site(client_user, 'Alpha', order=0)
        s_empty = _site(client_user, 'Bravo', order=1)  # active, no rows
        r = _report(client_user, '2026-06-01', '2026-06-30')
        _row(r, s_data, solar_yield=Decimal('120'))
        res = _agg(api_client, r, '2026-06-01', '2026-06-30')
        by_name = {x['site_name']: x for x in res.data['sites']}
        assert 'Bravo' in by_name
        assert by_name['Bravo']['solar_yield'] == '0.00'

    def test_retired_site_with_data_in_range_shown(self, api_client, client_user):
        s = _site(client_user, 'OldSite', is_active=False)
        r = _report(client_user, '2026-06-01', '2026-06-30')
        _row(r, s, solar_yield=Decimal('75'))
        res = _agg(api_client, r, '2026-06-01', '2026-06-30')
        by_name = {x['site_name']: x for x in res.data['sites']}
        assert 'OldSite' in by_name              # retired but has data -> shown
        assert by_name['OldSite']['solar_yield'] == '75.00'

    def test_retired_site_without_data_hidden(self, api_client, client_user):
        active = _site(client_user, 'Alpha')
        retired = _site(client_user, 'Ghost', is_active=False)  # no rows at all
        r = _report(client_user, '2026-06-01', '2026-06-30')
        _row(r, active, solar_yield=Decimal('10'))
        res = _agg(api_client, r, '2026-06-01', '2026-06-30')
        names = [x['site_name'] for x in res.data['sites']]
        assert 'Ghost' not in names              # retired + no data -> hidden
        assert 'Alpha' in names

    def test_missing_params_400(self, api_client, client_user):
        r = _report(client_user, '2026-06-01', '2026-06-30')
        assert api_client.get(
            f'/dashboard/reports/aggregate/?report_uuid={r.uuid}').status_code == 400

    def test_report_not_found_404(self, api_client):
        import uuid as uuidlib
        res = api_client.get(
            f'/dashboard/reports/aggregate/?report_uuid={uuidlib.uuid4()}'
            f'&from=2026-06-01&to=2026-06-30')
        assert res.status_code == 404
