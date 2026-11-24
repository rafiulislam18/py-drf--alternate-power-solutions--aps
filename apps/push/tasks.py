"""Celery tasks that send push notifications (queued by tickets.py / gas.py / jobs.py; tools.py hourly)."""

from celery import shared_task


@shared_task(ignore_result=True)
def push_ticket_message(message_id):
    """A new ticket chat message → the other side's devices."""
    from .tickets import send_ticket_message_push
    send_ticket_message_push(message_id)


@shared_task(ignore_result=True)
def push_low_gas(device_id, site_name, pct, critical=False, est_days=None):
    """A Gas Guard cylinder just went low (or almost empty) → its owner's and the Gas Guard admins' devices."""
    from .gas import send_low_gas_push
    send_low_gas_push(device_id, site_name, pct, critical, est_days)


@shared_task(ignore_result=True)
def push_job_assigned(job_id, user_ids, by_id=None):
    """Someone was added to a job → their devices (apps.push.jobs)."""
    from .jobs import send_job_assigned_push
    send_job_assigned_push(job_id, user_ids, by_id)


@shared_task
def push_overdue_tools():
    """Hourly 07:00–18:00 (migration 0003): bookings that just became overdue (apps.push.tools)."""
    from .tools import check_overdue_tools
    return check_overdue_tools()
