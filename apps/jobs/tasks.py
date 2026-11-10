"""Celery tasks for jobs: the "you've been assigned a job" emails."""

from celery import shared_task

from .emails import send_assignment_emails


@shared_task
def send_job_assignment_emails(job_id, user_ids, by_id=None):
    """Email the people newly assigned to a job (see apps.jobs.emails)."""
    return f'{send_assignment_emails(job_id, user_ids, by_id)} assignment email(s) sent'
