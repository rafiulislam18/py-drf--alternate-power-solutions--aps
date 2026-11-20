"""Celery tasks for jobs: the "you've been assigned a job" emails and the 07:00 Visits today run."""

from celery import shared_task
from django.utils import timezone

from .emails import send_assignment_emails, send_visits_today_emails


@shared_task
def send_job_assignment_emails(job_id, user_ids, by_id=None):
    """Email the people newly assigned to a job (see apps.jobs.emails)."""
    return f'{send_assignment_emails(job_id, user_ids, by_id)} assignment email(s) sent'


@shared_task
def send_visits_today():
    """Daily 07:00 (migration 0006): the Visits today email (if switched on in
    Job settings) and push. Nothing when there are no visits."""
    from apps.push.jobs import send_visits_today_push

    from .models import JobSettings
    from .services import todays_visits

    today = timezone.localdate()
    jobs = todays_visits()
    if not jobs:
        return 'no visits today'
    emails = send_visits_today_emails(jobs, today) if JobSettings.load().visits_email else 'off'
    push = send_visits_today_push(jobs, today)
    return f'{len(jobs)} visit(s); emails: {emails}; push: {push["sent"]} to {push["people"]} person(s)'
