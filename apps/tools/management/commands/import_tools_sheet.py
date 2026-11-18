"""
Import the Tool Bookings tab of the APS Operations sheet into the dashboard.

Download the sheet as .xlsx (File → Download → Microsoft Excel), copy it to
the server, then:

    python manage.py import_tools_sheet "/home/aps/APS Operations.xlsx" --dry-run   # check first, keeps nothing
    python manage.py import_tools_sheet "/home/aps/APS Operations.xlsx"             # import for real
    python manage.py archive_finished --tool-bookings                                # archive the returned ones

    python manage.py import_tools_sheet --undo     # local only (DEBUG=True): remove every imported tool and booking

The dry run lists every name in Booked By / Checked By, the person it's read
as and the staff account it matches (or would create), with the rows, so
spelling variants can be fixed (apps/jobs/sheet_import.py ALIASES) before the
real run. It runs once: tools would be doubled. No emails go out. How each
column is read: apps/tools/sheet_import.py.
"""

import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.tools.sheet_import import SheetError, import_tools, undo_import


def _rows(rows):
    rows = sorted(set(rows))
    return 'row ' + str(rows[0]) if len(rows) == 1 else 'rows ' + ', '.join(map(str, rows))


class Command(BaseCommand):
    help = 'Import the Tool Bookings tab of the APS Operations sheet (.xlsx). Use --dry-run first.'

    def add_arguments(self, parser):
        parser.add_argument('file', nargs='?', help='Path to the downloaded "APS Operations.xlsx".')
        parser.add_argument('--dry-run', action='store_true', help='Show what would be imported; keep nothing.')
        parser.add_argument('--undo', action='store_true',
                            help='Remove every imported tool and booking and their history (local only, DEBUG=True).')

    def handle(self, *args, file=None, dry_run=False, undo=False, **options):
        try:  # a Windows console would choke on the arrows in the report
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
        if undo:
            if not settings.DEBUG:
                raise CommandError('Refusing: --undo is for local testing only (DEBUG=True). '
                                   'On the live server, check with --dry-run before importing.')
            tools, bookings = undo_import()
            self.stdout.write(self.style.SUCCESS(f'Removed {tools} imported tools and {bookings} bookings.'))
            return
        if not file:
            raise CommandError('Give the path to the .xlsx file, e.g. import_tools_sheet "APS Operations.xlsx" --dry-run')
        path = Path(file).expanduser()
        if not path.is_file():
            raise CommandError(f'File not found: {path}')
        try:
            report = import_tools(str(path), dry_run=dry_run)
        except SheetError as exc:
            raise CommandError(str(exc))
        self.print_report(report, dry_run)

    def print_report(self, r, dry_run):
        w, ok, warn, head = self.stdout.write, self.style.SUCCESS, self.style.WARNING, self.style.MIGRATE_HEADING
        would = 'that would be ' if dry_run else ''
        w(head('DRY RUN — nothing was saved' if dry_run else 'Tool bookings sheet import'))
        w(f'Rows with something in them: {r.rows}')

        w(ok(f'Tools {would}imported: {len(r.tools)}'
             + (f'  ({r.tools[0][0]} to {r.tools[-1][0]})' if r.tools else '')))
        w('  Types (guessed from the name): ' + ', '.join(f'{k} {v}' for k, v in
                                                          sorted(r.types_guessed.items(), key=lambda kv: -kv[1])))
        for tool_id, name, type_name, qty in r.tools:
            w(f'  {tool_id}  {name}{f" ×{qty}" if qty > 1 else ""}  [{type_name}]')
        w(f'"In Office" rows (available, no booking): {r.in_office}')

        w(ok(f'Bookings {would}imported: {len(r.bookings)}'))
        for booking_id, person, out, tools, kit, state in r.bookings:
            extra = ' · long-term kit' if kit else ''
            w(f'  {booking_id}  {person}, out {out:%d %b %Y}{extra} · {state} · {len(tools)} tool(s): {", ".join(tools)}')
        if r.kits:
            w(f'Long-term kits: {", ".join(r.kits)}')
        if r.waiting:
            w(warn(f'Waiting for check after the import ({len(r.waiting)}): still out, no checker in the sheet'))
            for booking_id, person, what in r.waiting:
                w(f'  {booking_id}  {person} ({what})')
        if r.multi_unit:
            w(warn('Multi-unit tools booked as 1 unit each (the sheet has no count; fix on the dashboard if needed):'))
            for name, qty, ids in r.multi_unit:
                w(f'  {name} ×{qty}: {", ".join(ids)}')

        w(head('People (sheet spelling → read as → account)'))
        for spelling, name, outcome, rows in r.people:
            arrow = f' → {name}' if name != spelling else ''
            w(f'  "{spelling}"{arrow} → {outcome}  ({_rows(rows)})')
        for name, email, why in r.email_skipped:
            w(warn(f'  {name}: email {email} not set — {why}'))

        if r.notes:
            w(warn(f'Worth a look ({len(r.notes)}):'))
            for row, note in r.notes:
                w(f'  row {row}: {note}')
        if r.skipped:
            w(warn(f'Bookings not imported, no one in Booked By ({len(r.skipped)}); their tools are imported '
                   f'as available:'))
            for row, why in r.skipped:
                w(f'  row {row}: {why}')
        if dry_run:
            w(head('Looks right? Run the same command without --dry-run.'))
        else:
            w('Next: python manage.py archive_finished --tool-bookings  (archives the bookings that are all back)')
