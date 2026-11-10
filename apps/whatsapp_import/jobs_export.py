"""
Turn messages the ops manager marked as jobs into dashboard jobs (apps.jobs).

Each marked, not-yet-sent message becomes one job: category "Open jobs",
status Open, client = the chat name, task = the message text, and a first
comment "From WhatsApp — <sender> (<time>)". The message is then flagged
``exported_to_jobs_sheet`` (locked) and linked to its job, so it is never sent
twice. (Until 10/11/2026 this posted the messages to the Operations sheet
instead; the sheet is no longer used for jobs.)

Used by both the "Create jobs" button on the review page and the nightly
safety-net Celery task. Only one run at a time (see _export_lock), so the
button, the nightly task and its retries can't turn a message into two jobs.
"""

import logging
import uuid
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

from apps.jobs.models import Job, JobComment
from apps.jobs.services import default_category, save_with_next_number
from apps.records import audit

from .demo import DEMO_FILE_ID
from .models import WhatsAppMessage

logger = logging.getLogger(__name__)

# Postgres advisory-lock key for the export ("WA_JOBS" as hex). Any constant
# bigint works; it just has to be unique across the app's advisory locks.
_PG_LOCK_KEY = 0x57415F4A4F4253
# Fallback (non-Postgres, i.e. SQLite tests/dev) cache lock. The timeout only
# matters if a process dies holding it.
_CACHE_LOCK_KEY = 'whatsapp_import:jobs_export_lock'
_CACHE_LOCK_TIMEOUT = 10 * 60

COMMENT_AUTHOR = 'WhatsApp'


class ExportInProgress(Exception):
    """Raised when another run already holds the lock."""


@contextmanager
def _export_lock():
    """
    Serialise runs across processes (gunicorn workers + Celery).

    On Postgres this is a session-level advisory lock — Postgres drops it itself
    if the process/connection dies mid-run, so a killed worker can't leave it
    stuck. Elsewhere (SQLite) fall back to an atomic cache.add with a timeout.
    Raises ExportInProgress if the lock is already held.
    """
    if connection.vendor == 'postgresql':
        with connection.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(%s)', [_PG_LOCK_KEY])
            acquired = cur.fetchone()[0]
        if not acquired:
            raise ExportInProgress()
        try:
            yield
        finally:
            try:
                with connection.cursor() as cur:
                    cur.execute('SELECT pg_advisory_unlock(%s)', [_PG_LOCK_KEY])
            except Exception:  # connection gone — Postgres already released it
                logger.warning("Could not release jobs-export advisory lock.", exc_info=True)
        return

    token = uuid.uuid4().hex
    if not cache.add(_CACHE_LOCK_KEY, token, _CACHE_LOCK_TIMEOUT):
        raise ExportInProgress()
    try:
        yield
    finally:
        # Only release our own lock (it may have expired and been re-taken).
        if cache.get(_CACHE_LOCK_KEY) == token:
            cache.delete(_CACHE_LOCK_KEY)


def _sast(dt):
    """A stored (UTC) datetime as Cape Town wall-clock time."""
    if not dt:
        return ''
    from zoneinfo import ZoneInfo
    tz = getattr(settings, 'WHATSAPP_EXPORT_TIMEZONE', None) or 'Africa/Johannesburg'
    return timezone.localtime(dt, ZoneInfo(tz)).strftime('%d %b %Y %H:%M')


def pending_jobs_qs():
    """Messages marked as jobs (and not dismissed) but not yet turned into jobs.
    Demo messages (``manage.py seed_whatsapp_demo``) never become jobs."""
    return WhatsAppMessage.objects.filter(
        marked_as_job=True, dismissed=False, exported_to_jobs_sheet=False
    ).exclude(source_file__drive_file_id=DEMO_FILE_ID).order_by('sent_at')


def export_marked_jobs(user=None, limit=None):
    """
    Turn every pending marked message into a dashboard job.

    ``user`` is who clicked "Create jobs" (None = the nightly run). Returns
    {found, exported, job_ids}. Raises ExportInProgress if another run holds
    the lock.
    """
    with _export_lock():
        return _create_locked(user, limit)


def _create_locked(user, limit):
    qs = pending_jobs_qs()
    if limit is not None:
        qs = qs[:limit]
    pending = list(qs.values_list('pk', flat=True))
    stats = {'found': len(pending), 'exported': 0, 'job_ids': []}
    if not pending:
        return stats

    category = default_category()
    for pk in pending:
        with transaction.atomic():
            # Re-check under a row lock: unmarked / dismissed since we listed it → skip.
            message = (WhatsAppMessage.objects.select_for_update()
                       .filter(pk=pk, marked_as_job=True, dismissed=False, exported_to_jobs_sheet=False).first())
            if message is None:
                continue
            now = timezone.now()
            job = save_with_next_number(Job(
                category=category, client_name=(message.chat_name or message.sender)[:200], task=message.text,
                status=Job.Status.OPEN, date_added=now, last_updated=now, created_by=user,
            ))
            JobComment.objects.create(
                job=job, author=None, author_name=COMMENT_AUTHOR, created_at=now,
                body=f'From WhatsApp — {message.sender or "?"}'
                     + (f' ({_sast(message.sent_at)})' if message.sent_at else ''),
            )
            message.exported_to_jobs_sheet, message.exported_to_jobs_sheet_at, message.job = True, now, job
            message.save(update_fields=['exported_to_jobs_sheet', 'exported_to_jobs_sheet_at', 'job'])
            audit.log_create(user, job, summary=f'From WhatsApp ({message.chat_name or message.sender})')
        stats['exported'] += 1
        stats['job_ids'].append(job.job_id)

    logger.info("WhatsApp → jobs: %s", stats)
    return stats
