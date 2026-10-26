"""
Check / preview / run the weekly EnergiTrack solar report.

Usage:
    python manage.py weekly_solar_report --check
        For each site: confirm the token can read its EnergiTrack plant and how
        much of yesterday each role (solar / grid / battery) has data for.
    python manage.py weekly_solar_report [--from 2026-09-13] [--to 2026-09-20]
        Dry run: pull and print the calculated values plus any filled days /
        replaced spikes. Nothing is saved. --from is any day; --to defaults to
        7 days later (13/09 -> 20/09, the Sunday-to-Sunday shape the Monday task
        uses). Both inclusive. No dates = the week the next Monday run would report.
    python manage.py weekly_solar_report --from 2026-09-13 --save
        Save the report exactly like the Monday task (same skip rule, "report
        ready" email to APS + client, and warnings). Report date = the day after --to.
"""

from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.solar_dashboard.energitrack import REPORT_TZ, EnergiTrackClient, EnergiTrackError
from apps.solar_dashboard.models import Site
from apps.solar_dashboard.weekly_report import (
    SITES,
    WeeklyReportError,
    _norm,
    build_weekly_report,
    send_report_alert,
    send_report_ready_email,
)


def _parse_date(value, flag):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError(f"{flag} must be a date like 2026-09-13.")


class Command(BaseCommand):
    help = "Check EnergiTrack access, preview, or save the weekly solar report."

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help="Check each site's EnergiTrack plant and data.")
        parser.add_argument('--from', dest='start', help='First day, any weekday, YYYY-MM-DD.')
        parser.add_argument('--to', dest='end', help='Last day, YYYY-MM-DD (default: 7 days after --from).')
        parser.add_argument('--save', action='store_true', help='Save the report (default is a dry run).')

    def handle(self, *args, **options):
        if options['check']:
            return self._check()

        today, period = None, None
        if options['end'] and not options['start']:
            raise CommandError("--to needs --from.")
        if options['start']:
            start = _parse_date(options['start'], '--from')
            end = _parse_date(options['end'], '--to') if options['end'] else start + timedelta(days=7)
            period = (start, end)
            today = end + timedelta(days=1)

        try:
            result = build_weekly_report(today, period=period, dry_run=not options['save'])
        except WeeklyReportError as exc:
            raise CommandError(str(exc))

        self.stdout.write(result.summary())
        for site, fields, source in result.rows:
            battery = 'battery' if site.has_battery else 'no battery'
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{site.name}  [{source}, {battery}]"))
            for name, value in fields.items():
                self.stdout.write(f"  {name:<20} {value:>12}")
        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(f"! {warning}"))
        if result.report is not None:
            self.stdout.write(self.style.SUCCESS(f"\nSaved report {result.report.uuid}"))
            send_report_ready_email(result)
            send_report_alert(result)

    def _check(self):
        client = EnergiTrackClient()
        yesterday = datetime.now(REPORT_TZ).date() - timedelta(days=1)
        sites = {_norm(s.name): s for s in Site.objects.filter(
            client__username=getattr(settings, 'SOLAR_REPORT_CLIENT_USERNAME', 'urban-growth'))}
        for solar_site in SITES:
            label = f"{solar_site.name} -> plant {solar_site.plant_id}"
            site = sites.get(_norm(solar_site.name))
            has_battery = bool(site and site.has_battery)
            try:
                plant = client.get_plant(solar_site.plant_id)
                roles = ('solar', 'grid') + (('battery',) if has_battery else ())
                coverage = {
                    role: sum(b.get('covered_seconds') or 0
                              for b in client.role_hourly(solar_site.plant_id, role, yesterday, yesterday)) / 86400
                    for role in roles
                }
            except EnergiTrackError as exc:
                self.stdout.write(self.style.ERROR(f"x {label}: {exc}"))
                continue
            name = plant.get('name') if isinstance(plant, dict) else plant
            cov = ', '.join(f"{role} {c:.0%}" for role, c in coverage.items())
            style = self.style.SUCCESS if all(c >= 0.95 for c in coverage.values()) else self.style.WARNING
            note = '' if site else '  (site not found in dashboard!)'
            self.stdout.write(style(
                f"ok {label} ({name}), {'battery' if has_battery else 'no battery'} — "
                f"data yesterday: {cov}{note}"
            ))
