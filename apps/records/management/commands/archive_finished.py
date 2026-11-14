"""
Archive the jobs and tickets that were already finished before the archive
existed. Run it once after deploying the archive (the auto rule only ever
archives on a save that FINISHES an item, so older ones stay in the lists).

    python manage.py archive_finished --dry-run     # only count them
    python manage.py archive_finished               # jobs and tickets
    python manage.py archive_finished --jobs        # only jobs (or --tickets)

- Jobs: done / invoiced and Paid ("No charge" jobs aren't archived).
- Tickets: completed / cancelled.

They're archived as "automatically" by the system, with the rule as the
reason, and each one gets an "Archived" entry in its history. Items someone
has unarchived before are left alone, so running it again is safe.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.client_portal.models import Ticket
from apps.jobs.models import Job
from apps.records.models import ArchivableModel, AuditLog


def _never_unarchived(qs):
    """Leave out the items a person (or Undo) already brought back from the archive."""
    unarchived = AuditLog.objects.filter(object_type=qs.model._meta.label_lower, action='unarchived')
    return qs.exclude(pk__in=[int(pk) for pk in unarchived.values_list('object_id', flat=True) if pk.isdigit()])


def finished_jobs():
    return Job.objects.filter(archived_at__isnull=True, status__in=Job.FINISHED, payment=Job.Payment.PAID)


def finished_tickets():
    return Ticket.objects.filter(archived_at__isnull=True, status__in=Ticket.STATUS_GROUPS['completed'])


class Command(BaseCommand):
    help = 'Archive jobs (done / invoiced + Paid) and tickets (completed / cancelled) finished before the archive.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only count what would be archived.')
        parser.add_argument('--jobs', action='store_true', help='Only jobs.')
        parser.add_argument('--tickets', action='store_true', help='Only tickets.')

    def handle(self, *args, dry_run=False, jobs=False, tickets=False, **options):
        both = not jobs and not tickets
        kinds = [(label, qs) for label, qs, wanted in (('jobs', finished_jobs(), jobs or both),
                                                       ('tickets', finished_tickets(), tickets or both)) if wanted]
        with transaction.atomic():
            for label, qs in kinds:
                todo = _never_unarchived(qs)
                kept = qs.count() - todo.count()
                note = f' ({kept} left alone: unarchived by someone before)' if kept else ''
                if dry_run:
                    self.stdout.write(f'{label.capitalize()}: {todo.count()} would be archived{note}.')
                    continue
                done = 0
                for item in todo.order_by('pk'):
                    item.archive(None, ArchivableModel.Reason.AUTO, item.auto_archive_note())
                    done += 1
                self.stdout.write(self.style.SUCCESS(f'{label.capitalize()}: archived {done}{note}.'))
