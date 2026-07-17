"""Celery task for the nightly subscriptions-sheet sync."""

import logging

from celery import shared_task

from .exporter import SubscriptionSheetConfigError, sync_subscriptions

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=600)
def sync_subscriptions_to_sheet(self):
    """Push all valid subscriptions (both apps) to the APS Subscriptions sheet."""
    try:
        stats = sync_subscriptions()
    except SubscriptionSheetConfigError as exc:
        logger.error("Subscription sync skipped — sheet not configured: %s", exc)
        return f"skipped: {exc}"

    if stats.get('error'):
        logger.warning("Subscription sync error, will retry: %s", stats['error'])
        raise self.retry(exc=Exception(stats['error']))

    return (f"Subscription sync: {stats['created']} new, {stats['updated']} updated "
            f"({stats['sent']} sent).")
