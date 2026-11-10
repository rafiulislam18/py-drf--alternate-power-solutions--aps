"""
Delete jobs for good — e.g. after importing the wrong (old) copy of the sheet,
so the right one can be imported again.

    python manage.py delete_all_jobs                    # every job; asks you to type "delete all jobs"
    python manage.py delete_all_jobs --imported-only    # only the jobs that came from the sheet import
    python manage.py delete_all_jobs --yes              # don't ask (for scripts)

Jobs in the trash are deleted too, with their comments. WhatsApp messages that
had become one of these jobs are unlocked, so "Create jobs" can make them again.
Categories, colour settings and staff accounts are kept. The freed APS numbers
can be used again.

History: locally (DEBUG=True) the deleted jobs' history goes too. On the live
server audit history can never be deleted, so it stays and one more entry
records this deletion.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.jobs.models import Job, JobComment
from apps.records.models import AuditLog, Target
from apps.whatsapp_import.models import WhatsAppMessage

CONFIRM = 'delete all jobs'


class Command(BaseCommand):
    help = 'Delete every job (or only the imported ones) for good. Asks first.'

    def add_arguments(self, parser):
        parser.add_argument('--imported-only', action='store_true',
                            help='Only the jobs that came from the sheet import.')
        parser.add_argument('--yes', action='store_true', help="Don't ask for confirmation.")

    def handle(self, *args, imported_only=False, yes=False, **options):
        jobs = Job.all_objects.filter(imported_from_sheet=True) if imported_only else Job.all_objects.all()
        ids = list(jobs.values_list('pk', flat=True))
        if not ids:
            self.stdout.write('There are no jobs to delete.')
            return
        comments = JobComment.objects.filter(job_id__in=ids).count()
        messages = WhatsAppMessage.objects.filter(job_id__in=ids).count()
        what = 'imported jobs' if imported_only else 'jobs (all of them, trash included)'
        self.stdout.write(self.style.WARNING(
            f'This deletes {len(ids)} {what} and {comments} comments for good'
            + (f', and unlocks {messages} WhatsApp messages' if messages else '') + '.'))
        if not yes:
            answer = input(f'Type "{CONFIRM}" to go ahead: ')
            if answer.strip().lower() != CONFIRM:
                raise CommandError('Nothing was deleted.')

        history = self.delete(ids, imported_only)
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {len(ids)} jobs and {comments} comments.'
            + (f' Unlocked {messages} WhatsApp messages.' if messages else '')
            + (f' Removed {history} history entries (local).' if settings.DEBUG
               else ' Their history stays (live server) and this deletion is recorded.')))

    @staticmethod
    @transaction.atomic
    def delete(ids, imported_only):
        WhatsAppMessage.objects.filter(job_id__in=ids).update(
            job=None, exported_to_jobs_sheet=False, exported_to_jobs_sheet_at=None)
        Job.all_objects.filter(pk__in=ids).delete()
        if not settings.DEBUG:
            AuditLog.record(None, AuditLog.REMOVED,
                            Target('jobs.job', 'imported' if imported_only else 'all',
                                   'Imported jobs' if imported_only else 'All jobs'),
                            section='jobs', summary=f'{len(ids)} jobs deleted with manage.py delete_all_jobs')
            return 0
        table = AuditLog._meta.db_table
        removed = 0
        with connection.cursor() as cur:
            pg = connection.vendor == 'postgresql'
            if pg:
                cur.execute(f'ALTER TABLE {table} DISABLE TRIGGER records_auditlog_append_only')
            for start in range(0, len(ids), 500):
                chunk = [str(pk) for pk in ids[start:start + 500]]
                cur.execute(f"DELETE FROM {table} WHERE object_type = 'jobs.job' AND object_id IN "
                            f"({', '.join(['%s'] * len(chunk))})", chunk)
                removed += cur.rowcount
            if pg:
                cur.execute(f'ALTER TABLE {table} ENABLE TRIGGER records_auditlog_append_only')
        return removed
