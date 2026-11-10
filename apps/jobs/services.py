"""Making jobs from code other than the job form (e.g. WhatsApp messages)."""

from django.db import IntegrityError, transaction
from django.db.models import Max

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
