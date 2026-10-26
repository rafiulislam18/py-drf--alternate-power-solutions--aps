"""
Weekly solar report pulled from EnergiTrack (Urban Growth).

Every Monday 01:00 SAST (tasks.create_weekly_solar_report) we save ONE
SolarReport for the Sunday-to-Sunday week that just ended — e.g. 13/09 - 20/09,
then 20/09 - 27/09, the same way APS did them by hand (both Sundays inclusive,
so each Sunday appears in two reports — accepted). It covers all 7 Urban
Growth sites, each mapped to its EnergiTrack plant below.

Per site, per day (Africa/Johannesburg), kWh from EnergiTrack's configured roles:
    Solar yield        solar role
    Grid import        grid role, import          (helper: becomes Grid consumption)
    Sold to grid       grid role, export
    Battery charge     battery role, charge       battery sites only, else 0
    Used from battery  battery role, discharge    battery sites only, else 0

Daily clean-up before summing the week (each metric on its own):
    * Missing day (< 95% of the day has data): use the average per day of the
      previous week's complete days; if there are none, of this week's other
      complete days.
    * Grid import spike (>= 200% of that same average): use the average instead.
    Every fill / replacement is listed in the email + Telegram alert.

Calculated:
    Grid consumption   = grid import
    Usable solar       = solar yield - battery charge
    Est. saving (R)    = solar yield x saving tariff
    Sold to grid (R)   = sold to grid (kWh) x export tariff
    Total consumption  = grid import - sold to grid + used from battery + usable solar

Skip rule: if this client's existing reports already cover every day of the
period, nothing is created. If 2 or more (but not all) days are already covered
the report is still created, but a warning goes out on the solar-monitoring
email + Telegram. (1 day — the shared Sunday — is normal and stays quiet.)
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction

from .energitrack import REPORT_TZ, EnergiTrackClient, EnergiTrackError
from .models import Site, SiteData, SolarReport

logger = logging.getLogger(__name__)

BASE_METRICS = ('solar', 'grid_import', 'grid_export')
BATTERY_METRICS = ('battery_charge', 'battery_discharge')
METRIC_LABELS = {
    'solar': 'solar yield',
    'grid_import': 'grid import',
    'grid_export': 'sold to grid',
    'battery_charge': 'battery charge',
    'battery_discharge': 'used from battery',
}

DAY_COMPLETE_COVERAGE = 0.95   # below this share of the day, the day counts as missing
SPIKE_METRICS = ('grid_import',)
SPIKE_RATIO = 2.0              # >= 200% of the daily average is a spike

ZERO = Decimal('0.00')
TWO_PLACES = Decimal('0.01')


@dataclass(frozen=True)
class SolarSite:
    plant_id: int            # EnergiTrack plant
    name: str                # Site.name in the dashboard (matched loosely, see _norm)
    saving_tariff: Decimal   # R/kWh -> Est. saving
    export_tariff: Decimal   # R/kWh -> Sold to grid (R)


# Hardcoded for now (tariffs may move onto Site later). Order = report order.
SITES = (
    SolarSite(596, 'Paarl – Units 8 & 9', Decimal('1.97'), Decimal('0.95')),     # Paarl Unit 8 and 9
    SolarSite(301, 'Paarl* (with battery)', Decimal('1.97'), Decimal('0.95')),   # Urban Growth Paarl
    SolarSite(594, 'Stuart Close', Decimal('1.53'), Decimal('1.17')),            # Stuart cl Somerset West
    SolarSite(592, 'Springfield – Philippi', Decimal('1.87'), Decimal('1.17')),  # Fadel Springfield
    SolarSite(502, 'Rialto (with battery)', Decimal('1.87'), Decimal('1.17')),   # Urban Growth Montague
    SolarSite(668, 'Izuzu', Decimal('1.87'), Decimal('1.17')),                   # Isuzu Trucks Epping
    SolarSite(444, 'Henry Vos', Decimal('1.87'), Decimal('1.17')),               # Urban Growth Strand
)


class WeeklyReportError(Exception):
    """The weekly report can't be built at all (e.g. the client account is missing)."""


class SiteFetchFailed(Exception):
    """One or more sites failed; raised only when failures aren't allowed yet."""

    def __init__(self, failures):
        self.failures = failures
        super().__init__('; '.join(f"{name}: {err}" for name, err in failures.items()))


# ---------------------------------------------------------------------------
# Period, fetching and daily clean-up
# ---------------------------------------------------------------------------

def report_period(today):
    """(Sunday, Sunday) of the last full Sunday-to-Sunday week before `today`.

    Run on a Monday that's yesterday back to the Sunday before (13/09 - 20/09
    when run on 21/09). A Sunday never counts as finished on the day itself.
    """
    end = today - timedelta(days=(today.weekday() + 1) % 7 or 7)
    return end - timedelta(days=7), end


def _dates(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def fetch_daily(client, solar_site, has_battery, start, end):
    """{metric: {date: (kwh, coverage)}} for the site from `start` to `end`."""
    metrics = BASE_METRICS + (BATTERY_METRICS if has_battery else ())
    return client.daily_metrics(solar_site.plant_id, start, end, metrics)


def _avg(values):
    return sum(values) / len(values) if values else None


def clean_metric(metric, daily, prev_dates, period_dates):
    """(week total kWh, events) after filling missing days and replacing spikes.

    events: dicts with kind 'missing' | 'spike' | 'unfillable', day, metric,
    value (recorded kWh), coverage, avg, basis.
    """
    def complete(day):
        return daily[day][1] >= DAY_COMPLETE_COVERAGE

    prev_avg = _avg([daily[d][0] for d in prev_dates if complete(d)])
    total, events = 0.0, []
    for day in period_dates:
        value, coverage = daily[day]
        if prev_avg is not None:
            avg, basis = prev_avg, 'previous week'
        else:
            avg = _avg([daily[d][0] for d in period_dates if d != day and complete(d)])
            basis = 'other days this week'
        event = {'day': day, 'metric': metric, 'value': value, 'coverage': coverage, 'avg': avg, 'basis': basis}

        if not complete(day):
            if avg is None:
                events.append({**event, 'kind': 'unfillable'})
                total += value
            else:
                events.append({**event, 'kind': 'missing'})
                total += avg
        elif metric in SPIKE_METRICS and avg and value >= SPIKE_RATIO * avg:
            events.append({**event, 'kind': 'spike'})
            total += avg
        else:
            total += value
    return total, events


def describe_events(site_name, events):
    """Alert lines for one site's fills/replacements (missing days grouped per day)."""
    lines = []
    missing = {}
    for e in events:
        if e['kind'] == 'missing':
            missing.setdefault((e['day'], e['basis']), []).append(e)
    for (day, basis), group in sorted(missing.items(), key=lambda kv: kv[0][0]):
        coverage = min(e['coverage'] for e in group)
        filled = ', '.join(
            f"{METRIC_LABELS[e['metric']]} {e['avg']:,.2f} kWh (recorded {e['value']:,.2f})" for e in group
        )
        lines.append(
            f"{site_name} — {day:%a %d/%m}: data missing ({coverage:.0%} of the day), "
            f"filled with the {basis} average per day: {filled}."
        )
    for e in events:
        label = METRIC_LABELS[e['metric']]
        if e['kind'] == 'spike':
            lines.append(
                f"{site_name} — {e['day']:%a %d/%m}: {label} was {e['value']:,.2f} kWh, "
                f"{e['value'] / e['avg']:.0%} of the {e['avg']:,.2f} kWh average per day ({e['basis']}) "
                f"— replaced with {e['avg']:,.2f} kWh."
            )
        elif e['kind'] == 'unfillable':
            lines.append(
                f"{site_name} — {e['day']:%a %d/%m}: {label} data missing ({e['coverage']:.0%} of the day) "
                f"and no complete days to average from — kept the recorded {e['value']:,.2f} kWh."
            )
    return lines


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

def _dec(value):
    return Decimal(str(value or 0)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_fields(totals, has_battery, saving_tariff, export_tariff):
    """SiteData fields from one site's cleaned weekly kWh totals ({metric: kWh})."""
    solar = _dec(totals.get('solar'))
    grid_import = _dec(totals.get('grid_import'))
    sold = _dec(abs(totals.get('grid_export') or 0))
    charge = _dec(abs(totals.get('battery_charge') or 0)) if has_battery else ZERO
    used_battery = _dec(totals.get('battery_discharge')) if has_battery else ZERO

    usable = solar - charge
    return {
        'solar_yield': solar,
        'battery_charge': charge,
        'usable_solar': usable,
        'estimated_saving': _dec(solar * saving_tariff),
        'used_from_battery': used_battery,
        'sell_to_grid_kwh': sold,
        'sell_to_grid_r': _dec(sold * export_tariff),
        'grid_consumption': grid_import,
        'total_consumption': grid_import - sold + used_battery + usable,
    }


def empty_fields():
    return {f: ZERO for f in (
        'solar_yield', 'battery_charge', 'usable_solar', 'estimated_saving', 'used_from_battery',
        'sell_to_grid_kwh', 'sell_to_grid_r', 'grid_consumption', 'total_consumption',
    )}


def _norm(name):
    """Loose name match: case, spacing and dash style (– — -) don't matter."""
    name = re.sub(r'[‐-―-]', '-', name)
    return re.sub(r'\s+', ' ', name).strip().casefold()


def covered_days(client, start, end):
    """Days of [start, end] already covered by this client's existing reports."""
    days = set()
    reports = SolarReport.objects.filter(
        client=client, period_start__lte=end, period_end__gte=start,
    ).values_list('period_start', 'period_end')
    for ps, pe in reports:
        day = max(ps, start)
        while day <= min(pe, end):
            days.add(day)
            day += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Build + save
# ---------------------------------------------------------------------------

@dataclass
class WeeklyResult:
    status: str = ''                 # 'created' | 'skipped' | 'dry_run'
    report_date: object = None
    period_start: object = None
    period_end: object = None
    report: SolarReport = None
    rows: list = field(default_factory=list)       # [(Site, fields, source)] source: pulled|failed
    failed: dict = field(default_factory=dict)     # site name -> error
    already_covered: list = field(default_factory=list)  # dates already in other reports
    adjustments: list = field(default_factory=list)      # alert lines: filled days / replaced spikes
    warnings: list = field(default_factory=list)

    def summary(self):
        period = f"{self.period_start:%d/%m/%Y} - {self.period_end:%d/%m/%Y}"
        if self.status == 'skipped':
            return f"Weekly solar report {period}: skipped, every day already reported."
        pulled = sum(1 for _, _, src in self.rows if src == 'pulled')
        text = (
            f"Weekly solar report {period}: {self.status}, {len(self.rows)} sites "
            f"({pulled} pulled from EnergiTrack, {len(self.rows) - pulled} empty)."
        )
        if self.warnings:
            text += f" {len(self.warnings)} warning(s)."
        return text


def build_weekly_report(today=None, *, period=None, allow_failures=True, dry_run=False, client=None):
    """Build (and unless dry_run, save) the Urban Growth report.

    Covers report_period(today) — the Sunday-to-Sunday week that just ended —
    unless `period` gives an explicit (start, end), inclusive, any weekday.
    The report date is `today`.

    allow_failures=False raises SiteFetchFailed instead of saving when any
    site fails, so the task can retry before falling back to empty values.
    """
    today = today or datetime.now(REPORT_TZ).date()
    start, end = period or report_period(today)
    if end < start:
        raise WeeklyReportError(f"Period end {end:%d/%m/%Y} is before start {start:%d/%m/%Y}.")
    result = WeeklyResult(report_date=today, period_start=start, period_end=end)

    client = client or EnergiTrackClient()
    if not client.token:
        raise WeeklyReportError("ENERGITRACK_TOKEN is not set — cannot read EnergiTrack.")

    username = getattr(settings, 'SOLAR_REPORT_CLIENT_USERNAME', 'urban-growth')
    account = User.objects.filter(username=username).first()
    if account is None:
        raise WeeklyReportError(f"Client account '{username}' not found.")

    covered = covered_days(account, start, end)
    result.already_covered = sorted(covered)
    total_days = (end - start).days + 1
    if len(covered) == total_days:
        result.status = 'skipped'
        return result
    if len(covered) >= 2:
        result.warnings.append(
            f"{len(covered)} of the {total_days} days ({', '.join(f'{d:%d/%m}' for d in result.already_covered)}) "
            f"are already in another report for {username} — check for double counting."
        )

    sites_by_name = {}
    for site in Site.objects.filter(client=account).order_by('-is_active', 'order', 'name'):
        sites_by_name.setdefault(_norm(site.name), site)

    prev_start = start - timedelta(days=7)
    prev_dates = _dates(prev_start, start - timedelta(days=1))
    period_dates = _dates(start, end)
    for solar_site in SITES:
        site = sites_by_name.get(_norm(solar_site.name))
        if site is None:
            result.warnings.append(f"Site '{solar_site.name}' not found for {username} — left out of the report.")
            continue
        try:
            daily = fetch_daily(client, solar_site, site.has_battery, prev_start, end)
        except EnergiTrackError as exc:
            logger.warning("Weekly solar report: %s failed: %s", site.name, exc)
            result.failed[site.name] = str(exc)
            result.rows.append((site, empty_fields(), 'failed'))
            continue

        totals, events = {}, []
        for metric, days in daily.items():
            totals[metric], metric_events = clean_metric(metric, days, prev_dates, period_dates)
            events.extend(metric_events)
        result.adjustments.extend(describe_events(site.name, events))
        fields = calculate_fields(totals, site.has_battery, solar_site.saving_tariff, solar_site.export_tariff)
        result.rows.append((site, fields, 'pulled'))

    if result.failed and not allow_failures:
        raise SiteFetchFailed(result.failed)
    for name, err in result.failed.items():
        result.warnings.append(f"{name}: could not pull from EnergiTrack ({err}) — saved with empty values.")
    result.warnings.extend(result.adjustments)

    if dry_run:
        result.status = 'dry_run'
        return result

    with transaction.atomic():
        report = SolarReport.objects.create(
            client=account, report_date=today, period_start=start, period_end=end,
        )
        for order, (site, fields, _) in enumerate(result.rows):
            SiteData.objects.create(report=report, site=site, order=order, **fields)
    result.report = report
    result.status = 'created'
    return result


def _num(value):
    """SA-style whole number: 63 455."""
    return f"{value:,.0f}".replace(',', ' ')


def report_ready_recipients(result):
    """EMAIL_RECIPIENT (comma-separated allowed) + the client's email, deduplicated."""
    addresses = [a.strip() for a in (getattr(settings, 'EMAIL_RECIPIENT', '') or '').split(',')]
    if result.report is not None and result.report.client is not None:
        addresses.append((result.report.client.email or '').strip())
    seen, out = set(), []
    for addr in addresses:
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    return out


def send_report_ready_email(result):
    """One "your weekly report is ready" email to APS + the client after a successful save.

    Client-facing: headline totals, a per-site table and the report link — never
    the internal warnings (those go to APS only, via send_report_alert). Uses the
    same ALERT_EMAIL_ENABLED switch as the solar alerts, so dev runs stay quiet.
    """
    from apps.core.mail import ACCENT, button_html, send_html, wrap_html
    from django.utils.html import escape

    if result.report is None or not getattr(settings, 'ALERT_EMAIL_ENABLED', False):
        return False
    report = result.report
    try:
        company = report.client.client_profile.company_name or report.client.username
    except Exception:  # noqa: BLE001
        company = report.client.username
    period = f"{result.period_start:%d/%m/%Y} – {result.period_end:%d/%m/%Y}"
    url = f"{settings.FRONTEND_BASE_URL}/dashboard/{report.uuid}"

    rows = [(site, f) for site, f, _ in result.rows]
    totals = {k: sum((f[k] for _, f in rows), ZERO) for k in (
        'solar_yield', 'estimated_saving', 'sell_to_grid_r', 'grid_consumption')}
    tiles = ''.join(
        f'<td style="padding:10px; text-align:center; background:#f8f9fa; border-radius:6px;">'
        f'<div style="color:#666; font-size:12px;">{label}</div>'
        f'<div style="font-size:18px; font-weight:bold; color:#222;">{value}</div></td>'
        for label, value in (
            ('Total solar yield', f"{_num(totals['solar_yield'])} kWh"),
            ('Estimated savings', f"R {_num(totals['estimated_saving'])}"),
            ('Sold to grid', f"R {_num(totals['sell_to_grid_r'])}"),
            ('Grid consumption', f"{_num(totals['grid_consumption'])} kWh"),
        )
    )
    cell = 'padding:8px 10px; border-bottom:1px solid #eee;'
    site_rows = ''.join(
        f'<tr><td style="{cell}">{escape(site.name)}</td>'
        f'<td style="{cell} text-align:right;">{_num(f["solar_yield"])} kWh</td>'
        f'<td style="{cell} text-align:right;">R {_num(f["estimated_saving"])}</td></tr>'
        for site, f in rows
    )
    head = f'text-align:left; padding:8px 10px; border-bottom:2px solid {ACCENT}; color:{ACCENT};'
    body = f"""
        <p style="color:#333;">Hi {escape(company)},</p>
        <p style="color:#333;">Your weekly solar report for <strong>{period}</strong> is ready.</p>
        <table style="width:100%; border-collapse:separate; border-spacing:6px; margin:12px 0;"><tr>{tiles}</tr></table>
        <table style="width:100%; border-collapse:collapse; margin-top:12px;">
            <thead><tr>
                <th style="{head}">Site</th>
                <th style="{head} text-align:right;">Solar yield</th>
                <th style="{head} text-align:right;">Savings</th>
            </tr></thead>
            <tbody>{site_rows}</tbody>
        </table>
        {button_html(url, 'Open report')}
        <p style="color:#888; font-size:12px; margin-top:16px;">
            All values are engineering estimates for performance reporting only and do not
            represent municipal billing amounts or guaranteed savings.
        </p>
    """
    return send_html(
        f"Your weekly solar report is ready — {period}",
        wrap_html('Weekly Solar Report', body),
        report_ready_recipients(result),
        log_prefix='Weekly solar report',
    )


def send_report_alert(result=None, error=None):
    """Email + Telegram (the solar-monitoring channels) when the run needs attention."""
    from apps.fault_detection.alerts import dispatch_alert

    if error is not None:
        title = 'Weekly Solar Report Not Created'
        warnings = [str(error)]
    elif result is not None and result.warnings:
        title = 'Weekly Solar Report Created With Warnings'
        warnings = result.warnings
    else:
        return None

    context = {'kind': 'solar_report', 'title': title, 'warnings': warnings}
    if result is not None:
        context['period'] = f"{result.period_start:%d/%m/%Y} – {result.period_end:%d/%m/%Y}"
        context['sites'] = [(site.name, src) for site, _, src in result.rows]
        if result.report is not None:
            context['report_url'] = f"{settings.FRONTEND_BASE_URL}/dashboard/{result.report.uuid}"
    return dispatch_alert(subject=f"APS Solar Report: {title}", context=context)
