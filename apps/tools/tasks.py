"""Celery tasks for tool bookings: the alerts and the daily overdue digest (see apps.tools.emails)."""

from celery import shared_task

from . import emails


@shared_task
def send_booked_alert(booking_id):
    return f'booked-out alert sent: {emails.send_booked(booking_id)}'


@shared_task
def send_returned_alert(movement_id):
    return f'returned alert sent: {emails.send_returned(movement_id)}'


@shared_task
def send_flagged_alert(booking_id, movement_ids):
    return f'problem alert sent: {emails.send_flagged(booking_id, movement_ids)}'


@shared_task
def send_overdue_digest():
    """Daily 09:00 (migration 0003): one email listing the overdue bookings."""
    return emails.send_overdue_digest()
