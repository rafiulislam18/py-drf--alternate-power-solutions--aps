"""
Celery task for the nightly WhatsApp chat import.

Scheduled via django-celery-beat (see migration 0002) to run at 00:00 SAST every
night. The heavy lifting lives in services.import_new_files so it can also be run
on demand via `manage.py import_whatsapp`.
"""

import logging

from celery import shared_task

from .drive import DriveConfigError
from .jobs_export import JobsSheetConfigError, export_marked_jobs
from .services import import_new_files

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=600)
def import_whatsapp_chats(self):
    """Pull and store any new WhatsApp export files from the shared Drive folder."""
    try:
        stats = import_new_files()
    except DriveConfigError as exc:
        # Misconfiguration (missing token/folder) won't self-heal by retrying,
        # so log loudly and stop rather than burning retries.
        logger.error("WhatsApp import skipped — Drive not configured: %s", exc)
        return f"skipped: {exc}"
    except Exception as exc:  # transient Drive/network errors are worth a retry
        logger.exception("WhatsApp import failed; will retry.")
        raise self.retry(exc=exc)

    msg = (f"WhatsApp import: {stats['files_imported']}/{stats['files_new']} new files, "
           f"{stats['messages_created']} messages.")
    if stats['errors']:
        msg += f" {len(stats['errors'])} file error(s)."
    logger.info(msg)
    return msg


@shared_task(bind=True, max_retries=3, default_retry_delay=600)
def export_jobs_to_sheet(self):
    """
    Nightly safety-net: push any messages marked as jobs but not yet exported to
    the APS Open Jobs sheet. The ops manager usually pushes on demand via the
    review page; this sweeps up anything left over.
    """
    try:
        stats = export_marked_jobs()
    except JobsSheetConfigError as exc:
        logger.error("Jobs export skipped — sheet not configured: %s", exc)
        return f"skipped: {exc}"

    if stats.get('error'):
        # Delivery failed (network/sheet error) — retry; the messages stay
        # pending so nothing is lost.
        logger.warning("Jobs export had an error, will retry: %s", stats['error'])
        raise self.retry(exc=Exception(stats['error']))

    return f"Jobs export: {stats['exported']} pushed, {stats['found']} found."
