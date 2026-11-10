"""
Import the Jobs tab of the APS Operations sheet into the dashboard.

Download the sheet as .xlsx (File → Download → Microsoft Excel), copy it to
the server, then:

    python manage.py import_jobs_sheet "/home/aps/APS Operations.xlsx" --dry-run   # check first, keeps nothing
    python manage.py import_jobs_sheet "/home/aps/APS Operations.xlsx"             # import for real

    python manage.py import_jobs_sheet --undo     # local only (DEBUG=True): remove every imported job

Windows works the same: python manage.py import_jobs_sheet "C:\\Users\\Rafi\\Downloads\\APS Operations.xlsx"

Running it again with a newer download only adds the rows that aren't imported
yet (matched on the Job ID); jobs already imported are left as they are. No
emails go out. How each column is read: apps/jobs/sheet_import.py.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.jobs.sheet_import import SheetError, import_jobs, undo_import


class Command(BaseCommand):
    help = 'Import the Jobs tab of the APS Operations sheet (.xlsx). Use --dry-run first.'

    def add_arguments(self, parser):
        parser.add_argument('file', nargs='?', help='Path to the downloaded "APS Operations.xlsx".')
        parser.add_argument('--dry-run', action='store_true', help='Show what would be imported; keep nothing.')
        parser.add_argument('--undo', action='store_true',
                            help='Remove every imported job, its comments and history (local only, DEBUG=True).')

    def handle(self, *args, file=None, dry_run=False, undo=False, **options):
        if undo:
            if not settings.DEBUG:
                raise CommandError('Refusing: --undo is for local testing only (DEBUG=True). '
                                   'On the live server, check with --dry-run before importing.')
            self.stdout.write(self.style.SUCCESS(f'Removed {undo_import()} imported jobs.'))
            return
        if not file:
            raise CommandError('Give the path to the .xlsx file, e.g. import_jobs_sheet "APS Operations.xlsx" --dry-run')
        path = Path(file).expanduser()
        if not path.is_file():
            raise CommandError(f'File not found: {path}')
        try:
            report = import_jobs(str(path), dry_run=dry_run)
        except SheetError as exc:
            raise CommandError(str(exc))
        self.print_report(report, dry_run)

    def print_report(self, r, dry_run):
        w, ok, warn = self.stdout.write, self.style.SUCCESS, self.style.WARNING
        w(self.style.MIGRATE_HEADING('DRY RUN — nothing was saved' if dry_run else 'Jobs sheet import'))
        w(f'Rows with something in them: {r.rows}')
        w(ok(f'Jobs {"that would be " if dry_run else ""}imported: {r.created}'
             + (f'  (imported jobs now run {r.first} to {r.last})' if r.first else '')))
        if r.statuses:
            w('  By status: ' + ', '.join(f'{k} {v}' for k, v in sorted(r.statuses.items(), key=lambda kv: -kv[1])))
        if r.already_imported:
            w(f'Already imported earlier, left as they are: {len(r.already_imported)}')
        if r.skipped:
            w(warn(f'Skipped rows ({len(r.skipped)}):'))
            for row, why in r.skipped:
                w(f'  row {row}: {why}')
        if r.renumbered:
            w(warn(f'Given a new number ({len(r.renumbered)}):'))
            for row, old, new in r.renumbered:
                w(f'  row {row}: {old} → {new}')
        if r.categories_added:
            w(f'Categories added: {", ".join(r.categories_added)}')
        if r.people_created:
            w(f'Staff accounts added ({len(r.people_created)}, no password, no permissions):')
            for name, email in sorted(r.people_created.items()):
                w(f'  {name}' + (f' <{email}>' if email else '  (no email: add one to get job emails)'))
        if r.people_matched:
            w('Matched to existing accounts: ' + ', '.join(f'{n} → {u}' for n, u in sorted(r.people_matched.items())))
        for name, email, why in r.email_skipped:
            w(warn(f'  {name}: email {email} not set — {why}'))
        if r.notes:
            w(warn(f'Values kept as a note in the job\'s comment ({len(r.notes)}):'))
            for job_id, note in r.notes:
                w(f'  {job_id}: {note}')
        if dry_run:
            w(self.style.MIGRATE_HEADING('Looks right? Run the same command without --dry-run.'))
