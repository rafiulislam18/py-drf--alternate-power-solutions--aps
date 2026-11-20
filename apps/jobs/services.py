"""Making jobs from code other than the job form (e.g. WhatsApp messages), and
the "visits today" rule shared by the tile, the 07:00 email and the push."""

from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from .models import Job, JobCategory


def next_number():
    """One more than the highest number ever given (jobs in the trash included)."""
    return (Job.all_objects.aggregate(n=Max('number'))['n'] or 0) + 1


def save_with_next_number(job, attempts=5):
    """Give an unsaved ``job`` the next APS number and save it. Two saves at the
    same moment can race for one number; the loser simply takes the next."""
    for attempt in range(attempts):
        try:
            with transaction.atomic():
                job.number = next_number()
                job.save()
                return job
        except IntegrityError:
            if attempt == attempts - 1:
                raise
    return job


def default_category():
    """"Open jobs" — what the sheet gave jobs that came in from WhatsApp —
    else the first category still in use."""
    return (JobCategory.objects.filter(name__iexact='Open jobs', is_active=True).first()
            or JobCategory.objects.filter(is_active=True).first()
            or JobCategory.objects.get_or_create(name='Open jobs')[0])


def visiting_today():
    """Booked for today (local date). Cancelled and not-accepted jobs aren't
    visited, so they don't count."""
    return Q(scheduled_date=timezone.localdate()) & ~Q(status__in=(Job.Status.CANCELLED, Job.Status.NOT_ACCEPTED))


def todays_visits():
    """The jobs the Visits today tile counts (not archived, not in the trash),
    earliest time first; jobs without a time last."""
    jobs = list(Job.objects.filter(visiting_today(), archived_at__isnull=True)
                .select_related('category', 'site').prefetch_related('assigned_to'))
    return sorted(jobs, key=lambda j: (not j.scheduled_time.strip(), j.scheduled_time.strip().lower(), j.number))
