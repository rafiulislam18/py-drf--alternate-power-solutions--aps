"""
Push messages the ops manager marked as jobs into the APS Open Jobs Google Sheet.

The sheet has an Apps Script web-app `doPost` endpoint that appends a Jobs row per
message. We POST the marked-but-not-yet-exported messages, and on a successful
response mark them `exported_to_jobs_sheet=True` so they're never pushed twice.

Field mapping (agreed): chat_name -> Client, text -> Task, sender+sent_at kept in
the sheet's Comments column for traceability.

Used by both the manual "Push to jobs sheet" button (view) and the nightly
safety-net Celery task.
"""

import logging

import requests
from django.conf import settings
from django.utils import timezone

from .models import WhatsAppMessage

logger = logging.getLogger(__name__)


class JobsSheetConfigError(Exception):
    """Raised when the jobs-sheet URL/token isn't configured."""


def _sast(dt):
    """Format a stored (UTC) datetime back to Cape Town wall-clock for the sheet."""
    if not dt:
        return ''
    from zoneinfo import ZoneInfo
    tz = getattr(settings, 'WHATSAPP_EXPORT_TIMEZONE', None) or 'Africa/Johannesburg'
    return timezone.localtime(dt, ZoneInfo(tz)).strftime('%d %b %Y %H:%M')


def pending_jobs_qs():
    """Messages marked as jobs but not yet pushed to the sheet."""
    return WhatsAppMessage.objects.filter(
        marked_as_job=True, exported_to_jobs_sheet=False
    ).order_by('sent_at')


def export_marked_jobs(limit=None):
    """
    Push all pending marked-as-job messages to the jobs sheet.

    Returns a stats dict: {found, exported, skipped, error}. Idempotent — only
    flips the exported flag for messages the sheet confirms it created, so a
    partial/failed POST leaves them pending for the next run. Never raises for a
    delivery problem; raises JobsSheetConfigError only for missing config.
    """
    url = settings.WHATSAPP_JOBS_SHEET_URL
    token = settings.WHATSAPP_JOBS_SHEET_TOKEN
    if not url or not token:
        raise JobsSheetConfigError(
            "WHATSAPP_JOBS_SHEET_URL / WHATSAPP_JOBS_SHEET_TOKEN not configured."
        )

    qs = pending_jobs_qs()
    if limit is not None:
        qs = qs[:limit]
    messages = list(qs)

    stats = {'found': len(messages), 'exported': 0, 'skipped': 0, 'error': None}
    if not messages:
        return stats

    payload = {
        'token': token,
        'jobs': [
            {
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

    # The sheet created `created` rows for the messages we sent (in order). Mark
    # exactly those as exported. We trust order + count; if the sheet created
    # fewer than we sent, only mark the first N to stay consistent.
    created = int(data.get('created', 0))
    to_flag = messages[:created]
    now = timezone.now()
    WhatsAppMessage.objects.filter(pk__in=[m.pk for m in to_flag]).update(
        exported_to_jobs_sheet=True, exported_to_jobs_sheet_at=now,
    )
    stats['exported'] = len(to_flag)
    stats['skipped'] = len(messages) - len(to_flag)
    logger.info("Jobs-sheet export: %s", stats)
    return stats
