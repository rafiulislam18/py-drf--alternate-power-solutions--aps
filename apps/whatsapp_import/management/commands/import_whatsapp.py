"""
Run the WhatsApp chat import once, on demand (no Celery needed).

Same logic the nightly task runs. Handy for testing after setting up the
support@ Drive token, or for a manual catch-up.

Usage:
    python manage.py import_whatsapp
    python manage.py import_whatsapp --limit 1     # only the newest new file
"""

from django.core.management.base import BaseCommand

from apps.whatsapp_import.drive import DriveConfigError
from apps.whatsapp_import.services import import_new_files


class Command(BaseCommand):
    help = "Import new WhatsApp export files from the shared Google Drive folder."

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=None,
            help="Only import this many of the newest new files (for testing).",
        )

    def handle(self, *args, **options):
        try:
            stats = import_new_files(limit=options['limit'])
        except DriveConfigError as exc:
            self.stderr.write(self.style.ERROR(f"Drive not configured: {exc}"))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Seen {stats['files_seen']} file(s), {stats['files_new']} new, "
            f"imported {stats['files_imported']}, "
            f"{stats['messages_created']} message(s) created."
        ))
        for err in stats['errors']:
            self.stderr.write(self.style.WARNING(f"  ! {err['file']}: {err['error']}"))
