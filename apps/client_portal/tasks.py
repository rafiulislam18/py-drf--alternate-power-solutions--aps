"""Celery tasks for the ticket emails: unread chat messages and new tickets (both every 30 minutes)."""

from celery import shared_task

from .alerts import send_new_ticket_alerts as _send_new_ticket_alerts
from .digests import send_chat_digests


@shared_task
def send_ticket_chat_digests():
    """Email clients and the team about chat messages they haven't read."""
    result = send_chat_digests()
    if result['skipped']:
        return 'skipped: another run in progress'
    return f"{result['clients_emailed']} client email(s); team email {'sent' if result['team_emailed'] else 'not needed'}"


@shared_task
def send_new_ticket_alerts():
    """Email the team about new (non-emergency) tickets, and each client their confirmation."""
    result = _send_new_ticket_alerts()
    if result['skipped']:
        return 'skipped: another run in progress'
    return f"team told about {result['team_tickets']} ticket(s); {result['clients_emailed']} client confirmation(s)"
