"""Celery task for the nightly quotes-sheet sync (both quote tabs)."""

import logging

from celery import shared_task

from .exporter import QuoteSheetConfigError, sync_quotes

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=600)
def sync_quotes_to_sheet(self):
    """Push all quote requests (regular + container) to their sheet tabs."""
    try:
        results = sync_quotes()
    except QuoteSheetConfigError as exc:
        logger.error("Quote sync skipped — sheet not configured: %s", exc)
        return f"skipped: {exc}"

    errors = {k: v['error'] for k, v in results.items() if v.get('error')}
    if errors:
        logger.warning("Quote sync error(s), will retry: %s", errors)
        raise self.retry(exc=Exception(str(errors)))

    parts = []
    for kind, s in results.items():
        parts.append(f"{kind}: {s['created']} new, {s['updated']} updated ({s['sent']} sent)")
    return "Quote sync: " + "; ".join(parts) + "."
