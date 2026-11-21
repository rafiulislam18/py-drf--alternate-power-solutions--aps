"""Celery task: switch off cancelled plans once their paid month is over (see lifecycle.py)."""

from celery import shared_task

from . import lifecycle


@shared_task
def end_due_subscriptions():
    """Hourly (migration gg_subscription 0005)."""
    ended = lifecycle.end_due()
    return f"{ended['monitoring']} monitoring and {ended['swap']} add-on plan(s) ended"
