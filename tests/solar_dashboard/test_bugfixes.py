"""
Regression tests for the dashboard bug sweep: report period rules (reversed /
exact-duplicate), non-negative metrics, sites need a client, no username on public
responses, overlap-safe aggregation, malformed uuid, login throttling, strict
client creation, and stable list paging.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache

from apps.core.models import ClientProfile
from apps.solar_dashboard.models import Site, SiteData, SolarReport


@pytest.fixture(autouse=True)
def _clear_throttles():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def client_user(db, user_factory):
    u = user_factory(username='sunco')
    ClientProfile.objects.create(user=u, role='client', company_name='SunCo')
    return u


def _payload(client_id, start, end, sites):
    return {'client_id': client_id, 'report_date': end, 'period_start': start,
            'period_end': end, 'sites': sites}


def _report(client, start, end, rows):
    r = SolarReport.objects.create(client=client, report_date=end, period_start=start, period_end=end)
    for site, y in rows:
        SiteData.objects.create(report=r, site=site, solar_yield=Decimal(y))
    return r


@pytest.mark.django_db
class TestPeriodRules:

    def test_reversed_period_rejected(self, admin_api_client, client_user):
        s = Site.objects.create(client=client_user, name='A')
        res = admin_api_client.post('/dashboard/reports/', _payload(
            client_user.id, '2026-09-10', '2026-09-01', [{'site_id': s.id, 'solar_yield': '1'}]), format='json')
        assert res.status_code == 400
        assert 'period_end' in res.data

    def test_shared_boundary_day_allowed(self, admin_api_client, client_user):
        # APS convention: one period ends on the 7th, the next starts on the 7th.
        s = Site.objects.create(client=client_user, name='A')
        _report(client_user, date(2026, 9, 1), date(2026, 9, 7), [(s, 70)])
        res = admin_api_client.post('/dashboard/reports/', _payload(
            client_user.id, '2026-09-07', '2026-09-13', [{'site_id': s.id, 'solar_yield': '1'}]), format='json')
        assert res.status_code == 201, res.data

    def test_same_day_period_allowed(self, admin_api_client, client_user):
        s = Site.objects.create(client=client_user, name='A')
        res = admin_api_client.post('/dashboard/reports/', _payload(
            client_user.id, '2026-09-17', '2026-09-17', [{'site_id': s.id, 'solar_yield': '1'}]), format='json')
        assert res.status_code == 201, res.data

    def test_exact_duplicate_period_rejected(self, admin_api_client, client_user):
        s = Site.objects.create(client=client_user, name='A')
        _report(client_user, date(2026, 9, 1), date(2026, 9, 7), [(s, 70)])
        res = admin_api_client.post('/dashboard/reports/', _payload(
            client_user.id, '2026-09-01', '2026-09-07', [{'site_id': s.id, 'solar_yield': '1'}]), format='json')
        assert res.status_code == 400
        assert 'already has a report' in str(res.data['period_start'])

    def test_adjacent_period_and_other_client_allowed(self, admin_api_client, client_user, user_factory):
        s = Site.objects.create(client=client_user, name='A')
        _report(client_user, date(2026, 9, 1), date(2026, 9, 7), [(s, 70)])
        ok = admin_api_client.post('/dashboard/reports/', _payload(
            client_user.id, '2026-09-08', '2026-09-14', [{'site_id': s.id, 'solar_yield': '1'}]), format='json')
        assert ok.status_code == 201, ok.data
        other = user_factory(username='moon')
        ClientProfile.objects.create(user=other, role='client', company_name='Moon')
        m = Site.objects.create(client=other, name='M')
        same_dates = admin_api_client.post('/dashboard/reports/', _payload(
            other.id, '2026-09-01', '2026-09-07', [{'site_id': m.id, 'solar_yield': '1'}]), format='json')
        assert same_dates.status_code == 201, same_dates.data

    def test_editing_a_report_does_not_clash_with_itself(self, admin_api_client, client_user):
        s = Site.objects.create(client=client_user, name='A')
        r = _report(client_user, date(2026, 9, 1), date(2026, 9, 7), [(s, 70)])
        res = admin_api_client.put(f'/dashboard/reports/{r.uuid}/', _payload(
            client_user.id, '2026-09-01', '2026-09-07', [{'site_id': s.id, 'solar_yield': '80'}]), format='json')
        assert res.status_code == 200, res.data

    def test_negative_metric_rejected(self, admin_api_client, client_user):
        s = Site.objects.create(client=client_user, name='A')
        res = admin_api_client.post('/dashboard/reports/', _payload(
            client_user.id, '2026-09-01', '2026-09-07', [{'site_id': s.id, 'solar_yield': '-5'}]), format='json')
        assert res.status_code == 400
        assert not SolarReport.objects.exists()

    def test_sites_need_a_client(self, admin_api_client, client_user):
        s = Site.objects.create(client=client_user, name='A')
        res = admin_api_client.post('/dashboard/reports/', _payload(
            None, '2026-09-01', '2026-09-07', [{'site_id': s.id, 'solar_yield': '1'}]), format='json')
        assert res.status_code == 400
        assert 'client_id' in res.data


@pytest.mark.django_db
class TestPublicResponses:

    def test_public_report_and_aggregate_hide_username(self, api_client, client_user):
        s = Site.objects.create(client=client_user, name='A')
        r = _report(client_user, date(2026, 9, 1), date(2026, 9, 7), [(s, 70)])
        detail = api_client.get(f'/dashboard/reports/{r.uuid}/').data
        assert 'username' not in detail['client']
        assert detail['client']['company_name'] == 'SunCo'
        agg = api_client.get(f'/dashboard/reports/aggregate/?report_uuid={r.uuid}&from=2026-09-01&to=2026-09-07').data
        assert 'username' not in agg['client']

    def test_malformed_uuid_is_404_not_500(self, api_client):
        res = api_client.get('/dashboard/reports/aggregate/?report_uuid=abc&from=2026-09-01&to=2026-09-07')
        assert res.status_code == 404

    def test_legacy_overlapping_reports_are_not_double_counted(self, api_client, client_user):
        # Created directly (as older data might be) — the API now refuses this.
        s = Site.objects.create(client=client_user, name='A')
        r = _report(client_user, date(2026, 9, 1), date(2026, 9, 7), [(s, 70)])
        _report(client_user, date(2026, 9, 1), date(2026, 9, 7), [(s, 70)])
        agg = api_client.get(f'/dashboard/reports/aggregate/?report_uuid={r.uuid}&from=2026-09-01&to=2026-09-07').data
        assert Decimal(agg['sites'][0]['solar_yield']) == Decimal('70.00')

    def test_partial_overlap_splits_shared_days(self, api_client, client_user):
        # A: 1–10 Sep (100 → 10/day); B: 6–10 Sep (50 → 10/day). Days 6–10 are
        # shared, so they count half from each: 5*10 + 5*(5+5) = 100.
        s = Site.objects.create(client=client_user, name='A')
        r = _report(client_user, date(2026, 9, 1), date(2026, 9, 10), [(s, 100)])
        _report(client_user, date(2026, 9, 6), date(2026, 9, 10), [(s, 50)])
        agg = api_client.get(f'/dashboard/reports/aggregate/?report_uuid={r.uuid}&from=2026-09-01&to=2026-09-10').data
        assert Decimal(agg['sites'][0]['solar_yield']) == Decimal('100.00')


@pytest.mark.django_db
class TestLoginAndClients:

    def test_login_throttled_per_username(self, api_client, client_user):
        codes = [api_client.post('/dashboard/auth/token/', {'username': 'sunco', 'password': 'nope'},
                                 format='json').status_code for _ in range(11)]
        assert codes[:10] == [401] * 10
        assert codes[10] == 429

    def test_create_client_rejects_weak_password(self, admin_api_client):
        res = admin_api_client.post('/dashboard/clients/create/', {
            'username': 'newco', 'company_name': 'NewCo', 'password': 'short', 'confirm_password': 'short',
        }, format='multipart')
        assert res.status_code == 400
        assert 'password' in res.data
        assert not User.objects.filter(username='newco').exists()

    def test_create_client_rejects_bad_username(self, admin_api_client):
        res = admin_api_client.post('/dashboard/clients/create/', {
            'username': 'new co!', 'password': 'Sunny-Days-2026', 'confirm_password': 'Sunny-Days-2026',
        }, format='multipart')
        assert res.status_code == 400
        assert 'username' in res.data

    def test_create_client_with_optional_email(self, admin_api_client):
        res = admin_api_client.post('/dashboard/clients/create/', {
            'username': 'newco', 'company_name': 'NewCo', 'email': ' Ops@NewCo.CO.ZA ',
            'password': 'Sunny-Days-2026', 'confirm_password': 'Sunny-Days-2026',
        }, format='multipart')
        assert res.status_code == 201, res.data
        assert User.objects.get(username='newco').email == 'Ops@newco.co.za'

    def test_create_client_without_email(self, admin_api_client):
        for username, extra in (('noemail', {}), ('blankemail', {'email': ''})):
            res = admin_api_client.post('/dashboard/clients/create/', {
                'username': username, 'password': 'Sunny-Days-2026', 'confirm_password': 'Sunny-Days-2026', **extra,
            }, format='multipart')
            assert res.status_code == 201, res.data
            assert User.objects.get(username=username).email == ''

    def test_create_client_rejects_bad_email(self, admin_api_client):
        res = admin_api_client.post('/dashboard/clients/create/', {
            'username': 'newco', 'email': 'not-an-email',
            'password': 'Sunny-Days-2026', 'confirm_password': 'Sunny-Days-2026',
        }, format='multipart')
        assert res.status_code == 400
        assert 'email' in res.data
        assert not User.objects.filter(username='newco').exists()

    def test_create_client_is_atomic(self, admin_api_client, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError('disk full')
        monkeypatch.setattr(ClientProfile.objects, 'create', boom)
        with pytest.raises(RuntimeError):
            admin_api_client.post('/dashboard/clients/create/', {
                'username': 'newco', 'password': 'Sunny-Days-2026', 'confirm_password': 'Sunny-Days-2026',
            }, format='multipart')
        # No profile-less (= admin) user left behind.
        assert not User.objects.filter(username='newco').exists()

    def test_list_paging_is_stable_for_ties(self, admin_api_client, client_user):
        s = Site.objects.create(client=client_user, name='A')
        for week in range(12):
            start = date(2026, 1, 1 + week * 2)
            _report(client_user, start, start, [(s, 1)])
        seen = []
        for page in (1, 2):
            res = admin_api_client.get(f'/dashboard/reports/?sort=client_name&page={page}')
            seen += [r['uuid'] for r in res.data['results']]
        assert len(seen) == 12 and len(set(seen)) == 12


@pytest.mark.django_db
class TestListOrder:

    def test_reports_listed_by_period_start(self, admin_api_client, client_user):
        s = Site.objects.create(client=client_user, name='A')
        # Created out of order, and report_date deliberately not matching the period.
        mid = _report(client_user, date(2026, 8, 10), date(2026, 8, 16), [(s, 1)])
        new = _report(client_user, date(2026, 8, 17), date(2026, 8, 23), [(s, 1)])
        old = _report(client_user, date(2026, 8, 3), date(2026, 8, 9), [(s, 1)])
        SolarReport.objects.filter(pk=old.pk).update(report_date=date(2026, 12, 31))

        default = [r['uuid'] for r in admin_api_client.get('/dashboard/reports/').data['results']]
        assert default == [str(new.uuid), str(mid.uuid), str(old.uuid)]
        oldest = [r['uuid'] for r in admin_api_client.get('/dashboard/reports/?sort=period_start').data['results']]
        assert oldest == [str(old.uuid), str(mid.uuid), str(new.uuid)]
