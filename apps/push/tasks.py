"""Celery tasks that send push notifications (queued by tickets.py / gas.py)."""

from celery import shared_task


@shared_task(ignore_result=True)
def push_ticket_message(message_id):
    """A new ticket chat message → the other side's devices."""
    from .tickets import send_ticket_message_push
    send_ticket_message_push(message_id)


@shared_task(ignore_result=True)
def push_low_gas(device_id, site_name, pct):
    """A Gas Guard cylinder just went low → its owner's and the Gas Guard admins' devices."""
    from .gas import send_low_gas_push
    send_low_gas_push(device_id, site_name, pct)
