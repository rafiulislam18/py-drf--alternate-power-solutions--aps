"""
Push marked-as-job WhatsApp messages to the APS Open Jobs sheet, on demand.

Same logic the nightly task and the review page's button run.

Usage:
    python manage.py export_jobs
"""

from django.core.management.base import BaseCommand

from apps.whatsapp_import.jobs_export import JobsSheetConfigError, export_marked_jobs


class Command(BaseCommand):
    help = "Push marked-as-job messages to the APS Open Jobs Google Sheet."

    def handle(self, *args, **options):
        try:
            stats = export_marked_jobs()
        except JobsSheetConfigError as exc:
            self.stderr.write(self.style.ERROR(f"Jobs sheet not configured: {exc}"))
            return

        if stats.get('error'):
            self.stderr.write(self.style.ERROR(
                f"Export error: {stats['error']} ({stats['found']} were pending)."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Found {stats['found']}, exported {stats['exported']}, skipped {stats['skipped']}."
        ))
