"""
Archive the jobs, tickets and tool bookings that were already finished before
the archive existed (or came in from the Operations sheet). Run it once after deploying the archive (the auto rule only ever
archives on a save that FINISHES an item, so older ones stay in the lists).

    python manage.py archive_finished --dry-run     # only count them
    python manage.py archive_finished               # jobs, tickets and tool bookings
    python manage.py archive_finished --jobs        # only jobs (or --tickets, --tool-bookings)

- Jobs: done / invoiced and Paid or Write-off ("No charge" jobs aren't archived).
- Tickets: completed / cancelled.
- Tool bookings: every tool returned (checks may still be waiting).

They're archived as "automatically" by the system, with the rule as the
reason, and each one gets an "Archived" entry in its history. Items someone
has unarchived before are left alone, so running it again is safe.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.client_portal.models import Ticket
from apps.jobs.models import Job
from apps.records.models import ArchivableModel, AuditLog
from apps.tools.models import Booking, BookingItem


def _never_unarchived(qs):
    """Leave out the items a person (or Undo) already brought back from the archive."""
    unarchived = AuditLog.objects.filter(object_type=qs.model._meta.label_lower, action='unarchived')
    return qs.exclude(pk__in=[int(pk) for pk in unarchived.values_list('object_id', flat=True) if pk.isdigit()])


def finished_jobs():
    return Job.objects.filter(archived_at__isnull=True, status__in=Job.FINISHED, payment__in=Job.ARCHIVE_PAYMENTS)


def finished_tickets():
    return Ticket.objects.filter(archived_at__isnull=True, status__in=Ticket.STATUS_GROUPS['completed'])


def returned_tool_bookings():
    still_out = BookingItem.objects.filter(return_movement__isnull=True).values('booking_id')
    return Booking.objects.filter(archived_at__isnull=True, items__isnull=False).exclude(pk__in=still_out).distinct()


class Command(BaseCommand):
    help = ('Archive jobs (done / invoiced + Paid or Write-off), tickets (completed / cancelled) and tool bookings '
            '(every tool returned) finished before the archive.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only count what would be archived.')
        parser.add_argument('--jobs', action='store_true', help='Only jobs.')
        parser.add_argument('--tickets', action='store_true', help='Only tickets.')
        parser.add_argument('--tool-bookings', action='store_true', help='Only tool bookings.')

    def handle(self, *args, dry_run=False, jobs=False, tickets=False, tool_bookings=False, **options):
        every = not jobs and not tickets and not tool_bookings
        kinds = [(label, qs) for label, qs, wanted in (
            ('jobs', finished_jobs(), jobs or every),
            ('tickets', finished_tickets(), tickets or every),
            ('tool bookings', returned_tool_bookings(), tool_bookings or every)) if wanted]
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
