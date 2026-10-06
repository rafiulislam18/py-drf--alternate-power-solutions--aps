"""
Push messages the ops manager marked as jobs into the APS Open Jobs Google Sheet.

The sheet has an Apps Script web-app `doPost` endpoint that appends a Jobs row per
message. We POST the marked-but-not-yet-exported messages, and on a successful
response mark them `exported_to_jobs_sheet=True` so they're never pushed twice.

Field mapping (agreed): chat_name -> Client, text -> Task, sender+sent_at kept in
the sheet's Comments column for traceability. Each job also carries `message_id`
(our WhatsAppMessage pk) so the Apps Script can dedupe a re-sent message — e.g.
after a slow response that timed out here but still appended the rows.

Used by both the manual "Push to jobs sheet" button (view) and the nightly
safety-net Celery task. Only one export runs at a time (see _export_lock), so the
button, the nightly task and its retries can't send the same messages twice.
"""

import logging
import uuid
from contextlib import contextmanager

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

from .demo import DEMO_FILE_ID
from .models import WhatsAppMessage

logger = logging.getLogger(__name__)

# Postgres advisory-lock key for the export ("WA_JOBS" as hex). Any constant
# bigint works; it just has to be unique across the app's advisory locks.
_PG_LOCK_KEY = 0x57415F4A4F4253
# Fallback (non-Postgres, i.e. SQLite tests/dev) cache lock. The timeout only
# matters if a process dies holding it — well above the HTTP timeout below.
_CACHE_LOCK_KEY = 'whatsapp_import:jobs_export_lock'
_CACHE_LOCK_TIMEOUT = 10 * 60


class JobsSheetConfigError(Exception):
    """Raised when the jobs-sheet URL/token isn't configured."""


class ExportInProgress(Exception):
    """Raised when another jobs-sheet export already holds the lock."""


@contextmanager
def _export_lock():
    """
    Serialise exports across processes (gunicorn workers + Celery).

    On Postgres this is a session-level advisory lock — Postgres drops it itself
    if the process/connection dies mid-export, so a killed worker can't leave it
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
    """Format a stored (UTC) datetime back to Cape Town wall-clock for the sheet."""
    if not dt:
        return ''
    from zoneinfo import ZoneInfo
    tz = getattr(settings, 'WHATSAPP_EXPORT_TIMEZONE', None) or 'Africa/Johannesburg'
    return timezone.localtime(dt, ZoneInfo(tz)).strftime('%d %b %Y %H:%M')


def pending_jobs_qs():
    """Messages marked as jobs (and not dismissed) but not yet pushed to the sheet.
    Demo messages (``manage.py seed_whatsapp_demo``) are never pushed."""
    return WhatsAppMessage.objects.filter(
        marked_as_job=True, dismissed=False, exported_to_jobs_sheet=False
    ).exclude(source_file__drive_file_id=DEMO_FILE_ID).order_by('sent_at')


def export_marked_jobs(limit=None):
    """
    Push all pending marked-as-job messages to the jobs sheet.

    Returns a stats dict: {found, exported, skipped, unmarked_during_export,
    error}. Idempotent — only flips the exported flag for messages the sheet
    confirms it created, so a partial/failed POST leaves them pending for the next
    run. Never raises for a delivery problem; raises JobsSheetConfigError for
    missing config and ExportInProgress if another export is already running.
    """
    url = settings.WHATSAPP_JOBS_SHEET_URL
    token = settings.WHATSAPP_JOBS_SHEET_TOKEN
    if not url or not token:
        raise JobsSheetConfigError(
            "WHATSAPP_JOBS_SHEET_URL / WHATSAPP_JOBS_SHEET_TOKEN not configured."
        )

    with _export_lock():
        return _export_locked(url, token, limit)


def _export_locked(url, token, limit):
    """The export itself — the caller holds _export_lock."""
    qs = pending_jobs_qs()
    if limit is not None:
        qs = qs[:limit]
    messages = list(qs)

    stats = {'found': len(messages), 'exported': 0, 'skipped': 0,
             'unmarked_during_export': 0, 'error': None}
    if not messages:
        return stats

    payload = {
        'token': token,
        'jobs': [
            {
                'message_id': m.pk,         # stable id — lets the sheet dedupe re-sends
                'client': m.chat_name,      # -> "Client / Job" column
                'chat_name': m.chat_name,
                'task': m.text,             # -> "Task / Description" column
                'text': m.text,
                'sender': m.sender,
                'sent_at': _sast(m.sent_at),
            }
            for m in messages
        ],
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # network / non-2xx / bad JSON
        logger.error("Jobs-sheet export failed: %s", exc)
        stats['error'] = str(exc)
        return stats

    if not data.get('ok'):
        stats['error'] = data.get('error', 'unknown error from jobs sheet')
        logger.error("Jobs sheet rejected export: %s", stats['error'])
        return stats

    # Which messages does the sheet now hold? If it echoes `message_ids` (created
    # plus already-present duplicates) trust exactly those. Otherwise fall back to
    # order + count: the sheet created `created` rows for the messages we sent,
    # in order, so only the first N are flagged.
    sent_ids = [m.pk for m in messages]
    confirmed = data.get('message_ids')
    if isinstance(confirmed, list):
        confirmed_set = {str(i) for i in confirmed}
        to_flag = [pk for pk in sent_ids if str(pk) in confirmed_set]
    else:
        created = int(data.get('created', 0))
        to_flag = sent_ids[:created]

    # Re-check state in the UPDATE itself: a message unmarked or dismissed while
    # the POST was in flight must not be stamped exported (that would lock it).
    now = timezone.now()
    flagged = WhatsAppMessage.objects.filter(
        pk__in=to_flag, marked_as_job=True, dismissed=False, exported_to_jobs_sheet=False,
    ).update(exported_to_jobs_sheet=True, exported_to_jobs_sheet_at=now)

    stats['exported'] = flagged
    stats['skipped'] = len(messages) - len(to_flag)
    stats['unmarked_during_export'] = len(to_flag) - flagged
    if stats['unmarked_during_export']:
        # The sheet has rows for these but they're no longer jobs here — they
        # need removing from the sheet by hand.
        logger.warning(
            "Jobs-sheet export: %s message(s) changed during export and were not "
            "flagged, but the sheet accepted them.", stats['unmarked_during_export'],
        )
    logger.info("Jobs-sheet export: %s", stats)
    return stats
