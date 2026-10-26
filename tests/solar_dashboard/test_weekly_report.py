"""
Tests for the weekly EnergiTrack solar report (apps.solar_dashboard.weekly_report
+ apps.solar_dashboard.energitrack).

EnergiTrack itself is mocked (fetch_daily / EnergiTrackClient.role_hourly).
Covers the field maths, the Sunday-to-Sunday period, missing-day fills
(previous week, then this week's other days), grid-import spike replacement,
the skip / overlap rules, site-failure handling and the alert.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

import pytest

from apps.solar_dashboard import weekly_report
from apps.solar_dashboard.energitrack import EnergiTrackClient, _parse_reply
from apps.solar_dashboard.models import Site, SolarReport
from apps.solar_dashboard.weekly_report import (
    SiteFetchFailed, WeeklyReportError, build_weekly_report, calculate_fields, clean_metric,
    describe_events, report_period,
)
from apps.solar_dashboard.energitrack import EnergiTrackError

MONDAY = date(2026, 9, 21)                   # run date -> reports 13/09 - 20/09
START, END = date(2026, 9, 13), date(2026, 9, 20)
PREV = [START - timedelta(days=i) for i in range(7, 0, -1)]    # 06/09 - 12/09
PERIOD = [START + timedelta(days=i) for i in range(8)]

# Our sites: battery everywhere except Springfield and Stuart Close.
SITE_NAMES = [
    ('Paarl – Units 8 & 9', True), ('Paarl* (with battery)', True), ('Stuart Close', False),
    ('Springfield – Philippi', False), ('Rialto (with battery)', True), ('Izuzu', True),
    ('Henry Vos', True),
]

PER_DAY = {'solar': 100.0, 'grid_import': 50.0, 'grid_export': 10.0,
           'battery_charge': 20.0, 'battery_discharge': 15.0}


def daily(metrics, overrides=None, per_day=PER_DAY):
    """{metric: {date: (kwh, coverage)}} for PREV + PERIOD, full coverage unless overridden."""
    out = {m: {d: (per_day[m], 1.0) for d in PREV + PERIOD} for m in metrics}
    for (m, d), value in (overrides or {}).items():
        out[m][d] = value
    return out


def fake_fetch(overrides_by_site=None):
    def fetch(client, solar_site, has_battery, start, end):
        assert (start, end) == (PREV[0], END)
        metrics = weekly_report.BASE_METRICS + (weekly_report.BATTERY_METRICS if has_battery else ())
        return daily(metrics, (overrides_by_site or {}).get(solar_site.name))
    return fetch


class FakeClient:
    token = 'etc_test'


@pytest.fixture
def ug(db, user_factory):
    account = user_factory(username='urban-growth')
    for order, (name, battery) in enumerate(SITE_NAMES):
        Site.objects.create(client=account, name=name, has_battery=battery, order=order)
    return account


def _report(account, start, end):
    return SolarReport.objects.create(client=account, report_date=end, period_start=start, period_end=end)


def build(**kwargs):
    return build_weekly_report(MONDAY, client=FakeClient(), **kwargs)


class TestCalculations:

    def test_paarl_week(self):
        f = calculate_fields(
            {'solar': 19692.46, 'grid_import': 24424.9, 'grid_export': 3251.6,
             'battery_charge': 4083.8, 'battery_discharge': 2979.6},
            True, Decimal('1.97'), Decimal('0.95'))
        assert f['grid_consumption'] == Decimal('24424.90')     # = grid import
        assert f['usable_solar'] == Decimal('15608.66')
        assert f['total_consumption'] == Decimal('39761.56')     # import - export + discharge + usable
        assert f['estimated_saving'] == Decimal('38794.15')
        assert f['sell_to_grid_r'] == Decimal('3089.02')

    def test_big_exporter_total_stays_positive(self):
        # Stuart Close, 13-20/09: exports nearly everything it makes.
        f = calculate_fields({'solar': 22939.0, 'grid_import': 680.36, 'grid_export': 22507.83},
                             False, Decimal('1.53'), Decimal('1.17'))
        assert f['grid_consumption'] == Decimal('680.36')
        assert f['total_consumption'] == Decimal('1111.53')
        assert f['battery_charge'] == 0 and f['used_from_battery'] == 0

    def test_report_period_is_sunday_to_sunday(self):
        assert report_period(MONDAY) == (START, END)
        assert report_period(date(2026, 9, 23)) == (START, END)
        assert report_period(date(2026, 9, 20)) == (date(2026, 9, 6), date(2026, 9, 13))
        assert report_period(date(2026, 9, 28)) == (date(2026, 9, 20), date(2026, 9, 27))


class TestCleanMetric:

    def test_complete_week_is_plain_sum(self):
        total, events = clean_metric('solar', daily(['solar'])['solar'], PREV, PERIOD)
        assert total == 800.0 and events == []

    def test_missing_day_uses_previous_week_average(self):
        days = daily(['solar'], {('solar', d): (300.0, 1.0) for d in PREV})['solar']
        days[START] = (12.0, 0.40)            # 40% of the day -> missing
        total, events = clean_metric('solar', days, PREV, PERIOD)
        assert total == 7 * 100.0 + 300.0
        assert events[0]['kind'] == 'missing' and events[0]['basis'] == 'previous week'

    def test_no_previous_week_uses_other_days_this_week(self):
        days = daily(['solar'], {('solar', d): (0.0, 0.0) for d in PREV})['solar']
        days[START] = (0.0, 0.0)
        days[END] = (170.0, 1.0)
        total, events = clean_metric('solar', days, PREV, PERIOD)
        # other complete days: six at 100 + one at 170 -> 110 average
        assert total == pytest.approx(6 * 100.0 + 170.0 + 110.0)
        assert events[0]['basis'] == 'other days this week' and events[0]['avg'] == pytest.approx(110.0)

    def test_nothing_to_average_keeps_recorded(self):
        days = {d: (0.0, 0.0) for d in PREV + PERIOD}
        days[START] = (5.0, 0.5)
        total, events = clean_metric('grid_import', days, PREV, PERIOD)
        assert total == 5.0
        assert {e['kind'] for e in events} == {'unfillable'}

    def test_grid_import_spike_replaced_at_200_percent(self):
        days = daily(['grid_import'], {('grid_import', START): (100.0, 1.0),      # 200% -> spike
                                       ('grid_import', END): (99.0, 1.0)})['grid_import']  # 198% -> kept
        total, events = clean_metric('grid_import', days, PREV, PERIOD)
        assert total == 6 * 50.0 + 50.0 + 99.0
        assert [(e['kind'], e['day'], e['value'], e['avg']) for e in events] == [('spike', START, 100.0, 50.0)]

    def test_spike_rule_only_for_grid_import(self):
        days = daily(['solar'], {('solar', START): (400.0, 1.0)})['solar']
        total, events = clean_metric('solar', days, PREV, PERIOD)
        assert total == 7 * 100.0 + 400.0 and events == []

    def test_alert_lines_have_value_percentage_and_fill(self):
        days = daily(['grid_import'], {('grid_import', START): (125.0, 1.0)})['grid_import']
        _, events = clean_metric('grid_import', days, PREV, PERIOD)
        line = describe_events('Rialto', events)[0]
        assert 'Rialto' in line and 'Sun 13/09' in line
        assert '125.00 kWh' in line and '250%' in line and 'replaced with 50.00 kWh' in line

    def test_missing_lines_grouped_per_day(self):
        overrides = {(m, START): (1.0, 0.2) for m in ('solar', 'grid_import')}
        data = daily(['solar', 'grid_import'], overrides)
        events = []
        for m, d in data.items():
            events += clean_metric(m, d, PREV, PERIOD)[1]
        lines = describe_events('Izuzu', events)
        assert len(lines) == 1
        assert 'data missing (20% of the day)' in lines[0]
        assert 'solar yield 100.00 kWh (recorded 1.00)' in lines[0]
        assert 'grid import 50.00 kWh (recorded 1.00)' in lines[0]


@pytest.mark.django_db
class TestBuildWeeklyReport:

    def test_creates_one_report_with_all_sites(self, ug):
        with mock.patch.object(weekly_report, 'fetch_daily', side_effect=fake_fetch()):
            result = build()
        assert result.status == 'created' and result.warnings == []
        report = result.report
        assert (report.report_date, report.period_start, report.period_end) == (MONDAY, START, END)
        rows = {r.site.name: r for r in report.sites.select_related('site')}
        assert list(rows) == [n for n, _ in SITE_NAMES]
        paarl = rows['Paarl* (with battery)']
        assert paarl.solar_yield == Decimal('800.00')
        assert paarl.grid_consumption == Decimal('400.00')
        assert paarl.battery_charge == Decimal('160.00')
        assert paarl.total_consumption == Decimal('400') - 80 + 120 + (800 - 160)
        stuart = rows['Stuart Close']                 # no battery -> battery fields 0
        assert stuart.battery_charge == 0 and stuart.used_from_battery == 0

    def test_fills_and_spikes_become_warnings(self, ug):
        overrides = {'Rialto (with battery)': {('grid_import', START): (150.0, 1.0)},
                     'Izuzu': {('solar', END): (0.0, 0.0)}}
        with mock.patch.object(weekly_report, 'fetch_daily', side_effect=fake_fetch(overrides)):
            result = build()
        assert result.status == 'created'
        assert any(w.startswith('Rialto') and '300%' in w for w in result.warnings)
        assert any(w.startswith('Izuzu') and 'data missing' in w for w in result.warnings)
        rows = {r.site.name: r for r in result.report.sites.select_related('site')}
        assert rows['Rialto (with battery)'].grid_consumption == Decimal('400.00')   # spike -> average
        assert rows['Izuzu'].solar_yield == Decimal('800.00')                       # missing -> average

    def test_skips_when_every_day_exists(self, ug):
        _report(ug, date(2026, 9, 10), date(2026, 9, 16))
        _report(ug, date(2026, 9, 17), date(2026, 9, 25))
        with mock.patch.object(weekly_report, 'fetch_daily') as fetch:
            result = build()
        assert result.status == 'skipped'
        fetch.assert_not_called()

    def test_partly_covered_still_creates_with_warning(self, ug):
        _report(ug, date(2026, 9, 14), date(2026, 9, 19))
        with mock.patch.object(weekly_report, 'fetch_daily', side_effect=fake_fetch()):
            result = build()
        assert result.status == 'created'
        assert len(result.warnings) == 1 and '6 of the 8 days' in result.warnings[0]

    def test_shared_sunday_no_warning(self, ug):
        _report(ug, date(2026, 9, 6), date(2026, 9, 13))   # last week's report
        with mock.patch.object(weekly_report, 'fetch_daily', side_effect=fake_fetch()):
            result = build()
        assert result.status == 'created' and result.warnings == []

    def test_failed_site_raises_until_allowed(self, ug):
        ok = fake_fetch()

        def fetch(client, solar_site, *args):
            if solar_site.name == 'Henry Vos':
                raise EnergiTrackError('HTTP 502')
            return ok(client, solar_site, *args)
        with mock.patch.object(weekly_report, 'fetch_daily', side_effect=fetch):
            with pytest.raises(SiteFetchFailed):
                build(allow_failures=False)
            assert SolarReport.objects.count() == 0
            result = build(allow_failures=True)
        assert any('Henry Vos' in w and 'HTTP 502' in w for w in result.warnings)
        assert result.report.sites.get(site__name='Henry Vos').solar_yield == 0

    def test_missing_token_is_an_error(self, ug):
        client = FakeClient()
        client.token = ''
        with pytest.raises(WeeklyReportError):
            build_weekly_report(MONDAY, client=client)

    def test_dry_run_saves_nothing(self, ug):
        with mock.patch.object(weekly_report, 'fetch_daily', side_effect=fake_fetch()):
            result = build(dry_run=True)
        assert result.status == 'dry_run' and len(result.rows) == 7
        assert SolarReport.objects.count() == 0


@pytest.mark.django_db
class TestTask:

    def test_task_sends_alert_with_spike_details(self, ug):
        from apps.solar_dashboard.tasks import create_weekly_solar_report
        overrides = {'Paarl* (with battery)': {('grid_import', START): (200.0, 1.0)}}
        with mock.patch.object(weekly_report, 'fetch_daily', side_effect=fake_fetch(overrides)), \
             mock.patch('apps.solar_dashboard.tasks.build_weekly_report',
                        side_effect=lambda **kw: build(**kw)), \
             mock.patch('apps.fault_detection.alerts.dispatch_alert') as dispatch:
            create_weekly_solar_report.apply()
        dispatch.assert_called_once()
        context = dispatch.call_args.kwargs['context']
        assert context['kind'] == 'solar_report'
        assert any('400%' in w and 'replaced with 50.00 kWh' in w for w in context['warnings'])
        assert context['report_url'].endswith(str(SolarReport.objects.latest('created_at').uuid))

    def test_alert_renders(self):
        from apps.fault_detection.alerts import _build_email_html, _build_telegram_html
        ctx = {'kind': 'solar_report', 'title': 'Weekly Solar Report Created With Warnings',
               'warnings': ['Henry Vos: failed'], 'period': '13/09/2026 – 20/09/2026',
               'sites': [('Henry Vos', 'failed')], 'report_url': 'https://x/dashboard/abc'}
        assert 'Henry Vos: failed' in _build_email_html(ctx)
        assert 'https://x/dashboard/abc' in _build_telegram_html(ctx)


class TestEnergiTrackClient:

    def test_daily_metrics_folds_hourly_buckets_into_sast_days(self):
        # 12/09 22:00Z is 13/09 00:00 SAST; 13/09 21:00Z is the last hour of 13/09.
        buckets = [
            {'start': '2026-09-12T22:00:00Z', 'covered_seconds': 3600,
             'positive_energy': 1.0, 'negative_energy_magnitude': 4.0},
            {'start': '2026-09-13T21:00:00Z', 'covered_seconds': 1800,
             'positive_energy': 2.0, 'negative_energy_magnitude': None},
            {'start': '2026-09-13T22:00:00Z', 'covered_seconds': 3600,
             'positive_energy': 5.0, 'negative_energy_magnitude': 1.0},
        ]
        client = EnergiTrackClient(token='t')
        with mock.patch.object(EnergiTrackClient, 'role_hourly', return_value=buckets) as hourly:
            out = client.daily_metrics(1, date(2026, 9, 13), date(2026, 9, 14), ('grid_import', 'grid_export'))
        hourly.assert_called_once_with(1, 'grid', date(2026, 9, 13), date(2026, 9, 14))
        assert out['grid_export'][date(2026, 9, 13)] == (3.0, 5400 / 86400)
        assert out['grid_import'][date(2026, 9, 13)][0] == 4.0
        assert out['grid_export'][date(2026, 9, 14)] == (5.0, 3600 / 86400)

    def test_long_ranges_split_into_31_day_calls(self):
        client = EnergiTrackClient(token='t')
        with mock.patch.object(EnergiTrackClient, 'role_hourly', return_value=[]) as hourly:
            client.daily_metrics(1, date(2026, 8, 1), date(2026, 9, 10), ('solar',))
        assert [c.args[2:] for c in hourly.call_args_list] == [
            (date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 9, 1), date(2026, 9, 10))]

    def test_parse_event_stream_reply(self):
        resp = mock.Mock(headers={'content-type': 'text/event-stream'},
                         text='event: message\ndata: {"jsonrpc":"2.0","id":3,"result":{"ok":1}}\n\n')
        assert _parse_reply(resp, 3) == {'jsonrpc': '2.0', 'id': 3, 'result': {'ok': 1}}


@pytest.mark.django_db
class TestReportReadyEmail:

    @pytest.fixture
    def saved(self, ug):
        ug.email = 'client@urbangrowth.example'
        ug.save()
        overrides = {'Rialto (with battery)': {('grid_import', START): (150.0, 1.0)}}  # an internal warning
        with mock.patch.object(weekly_report, 'fetch_daily', side_effect=fake_fetch(overrides)):
            return build()

    def test_one_email_to_aps_and_client(self, saved, settings, mailoutbox):
        settings.ALERT_EMAIL_ENABLED = True
        settings.EMAIL_RECIPIENT = 'ops@aps.example, CLIENT@urbangrowth.example'
        assert weekly_report.send_report_ready_email(saved) is True
        assert len(mailoutbox) == 1
        msg = mailoutbox[0]
        assert msg.to == ['ops@aps.example', 'CLIENT@urbangrowth.example']   # deduplicated
        assert '13/09/2026 – 20/09/2026' in msg.subject
        assert 'Henry Vos' in msg.body and str(saved.report.uuid) in msg.body
        assert '5 600 kWh' in msg.body          # 7 sites x 800 kWh solar
        assert 'replaced' not in msg.body       # internal warnings never go to the client

    def test_no_client_email_sends_to_aps_only(self, saved, settings, mailoutbox):
        settings.ALERT_EMAIL_ENABLED = True
        settings.EMAIL_RECIPIENT = 'ops@aps.example'
        saved.report.client.email = ''
        weekly_report.send_report_ready_email(saved)
        assert mailoutbox[0].to == ['ops@aps.example']

    def test_disabled_sends_nothing(self, saved, settings, mailoutbox):
        settings.ALERT_EMAIL_ENABLED = False
        assert weekly_report.send_report_ready_email(saved) is False
        assert mailoutbox == []

    def test_task_sends_ready_email_even_without_warnings(self, ug):
        from apps.solar_dashboard.tasks import create_weekly_solar_report
        with mock.patch.object(weekly_report, 'fetch_daily', side_effect=fake_fetch()), \
             mock.patch('apps.solar_dashboard.tasks.build_weekly_report',
                        side_effect=lambda **kw: build(**kw)), \
             mock.patch('apps.solar_dashboard.tasks.send_report_ready_email') as ready, \
             mock.patch('apps.fault_detection.alerts.dispatch_alert') as dispatch:
            create_weekly_solar_report.apply()
        ready.assert_called_once()
        dispatch.assert_not_called()

    def test_skipped_week_sends_nothing(self, ug):
        from apps.solar_dashboard.tasks import create_weekly_solar_report
        _report(ug, START, END)
        with mock.patch('apps.solar_dashboard.tasks.build_weekly_report',
                        side_effect=lambda **kw: build(**kw)), \
             mock.patch('apps.solar_dashboard.tasks.send_report_ready_email') as ready:
            create_weekly_solar_report.apply()
        ready.assert_not_called()
