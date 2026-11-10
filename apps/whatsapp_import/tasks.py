"""
Celery task for the nightly WhatsApp chat import.

Scheduled via django-celery-beat (see migration 0002) to run at 00:00 SAST every
night. The heavy lifting lives in services.import_new_files so it can also be run
on demand via `manage.py import_whatsapp`.
"""

import logging

from celery import shared_task

from .drive import DriveConfigError
from .jobs_export import ExportInProgress, export_marked_jobs
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


@shared_task
def create_jobs_from_whatsapp():
    """
    Nightly safety-net: turn any messages marked as jobs but not yet sent on
    into dashboard jobs. The ops manager usually does it with "Create jobs" on
    the review page; this sweeps up anything left over.
    """
    try:
        stats = export_marked_jobs()
    except ExportInProgress:
        # A button press is already doing it — it picks up the same messages.
        logger.info("WhatsApp → jobs skipped — another run is in progress.")
        return "skipped: already running"
    return f"WhatsApp → jobs: {stats['exported']} created, {stats['found']} found."
