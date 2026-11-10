"""
Turn marked-as-job WhatsApp messages into dashboard jobs, on demand.

Same logic the nightly task and the review page's "Create jobs" button run.

Usage:
    python manage.py export_jobs
"""

from django.core.management.base import BaseCommand

from apps.whatsapp_import.jobs_export import ExportInProgress, export_marked_jobs


class Command(BaseCommand):
    help = "Turn marked-as-job WhatsApp messages into dashboard jobs."

    def handle(self, *args, **options):
        try:
            stats = export_marked_jobs()
        except ExportInProgress:
            self.stderr.write(self.style.ERROR("Jobs are already being created; try again shortly."))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Found {stats['found']}, created {stats['exported']} job(s)"
            + (f": {', '.join(stats['job_ids'])}" if stats['job_ids'] else '.')
        ))
