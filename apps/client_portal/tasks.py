"""Celery task for the ticket-chat unread-message emails (every 30 minutes)."""

from celery import shared_task

from .digests import send_chat_digests


@shared_task
def send_ticket_chat_digests():
    """Email clients and the team about chat messages they haven't read."""
    result = send_chat_digests()
    if result['skipped']:
        return 'skipped: another run in progress'
    return f"{result['clients_emailed']} client email(s); team email {'sent' if result['team_emailed'] else 'not needed'}"
