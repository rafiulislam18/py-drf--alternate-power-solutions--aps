"""
Ingestion orchestration for the WhatsApp chat import.

Pulls new export files from the shared Drive folder, parses each into messages,
and stores one row per message. Kept free of Celery so it can be run from a
management command or a test directly.

Flow per run:
  1. List files in the folder (support@ Drive).
  2. Skip any whose Drive file ID we've already imported (ImportedFile).
  3. Download + parse each new file.
  4. Localise each message's naive timestamp to SAST (exact exported wall-clock).
  5. Bulk-insert, ignoring conflicts (the unique constraint dedupes messages).
  6. Record the file as imported.
"""

import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .drive import (
    DriveConfigError, get_chat_text, get_drive_service, list_export_files,
)
from .models import ImportedFile, WhatsAppMessage
from .parser import parse_chat_export

logger = logging.getLogger(__name__)


def _sast_tz():
    """The timezone the team exports in (Cape Town / SAST)."""
    tz_name = getattr(settings, 'WHATSAPP_EXPORT_TIMEZONE', None) or 'Africa/Johannesburg'
    return ZoneInfo(tz_name)


def _chat_name_from_filename(name):
    """Best-effort conversation label from the export filename.

    The live exports are named like 'WhatsApp Chat with House Anderson' (no
    extension); some tooling adds '.txt'/'.zip'. Strip the boilerplate prefix and
    any trailing extension for a clean label like 'House Anderson'.
    """
    label = name
    if label.lower().endswith(('.txt', '.zip')):
        label = label.rsplit('.', 1)[0]
    for prefix in ('WhatsApp Chat with ', 'WhatsApp Chat - ', 'WhatsApp Chat '):
        if label.startswith(prefix):
            return label[len(prefix):].strip()
    return label.strip()


def import_new_files(service=None, limit=None):
    """
    Import all not-yet-seen export files from the shared folder.

    Returns a stats dict: files_seen, files_new, files_imported,
    messages_created, and any per-file errors. Never raises for a single bad
    file — it logs and continues so one file can't block the rest.
    """
    service = service or get_drive_service()

    files = list_export_files(service)
    seen_ids = set(
        ImportedFile.objects.filter(
            drive_file_id__in=[f['id'] for f in files]
        ).values_list('drive_file_id', flat=True)
    )
    new_files = [f for f in files if f['id'] not in seen_ids]
    if limit is not None:
        new_files = new_files[:limit]

    stats = {
        'files_seen': len(files),
        'files_new': len(new_files),
        'files_imported': 0,
        'messages_created': 0,
        'errors': [],
    }

    tz = _sast_tz()

    for f in new_files:
        try:
            created = _import_one_file(service, f, tz)
            stats['files_imported'] += 1
            stats['messages_created'] += created
        except Exception as exc:  # noqa: BLE001 — one bad file must not abort the run
            logger.exception("Failed to import WhatsApp export %s (%s)", f.get('name'), f.get('id'))
            stats['errors'].append({'file': f.get('name'), 'id': f.get('id'), 'error': str(exc)})

    logger.info("WhatsApp import complete: %s", stats)
    return stats


@transaction.atomic
def _import_one_file(service, drive_file, tz):
    """Download, parse and store one export file. Returns messages created."""
    text = get_chat_text(service, drive_file)
    parsed = parse_chat_export(text)

    imported_file = ImportedFile.objects.create(
        drive_file_id=drive_file['id'],
        name=drive_file.get('name', ''),
        drive_modified_time=drive_file.get('modifiedTime', ''),
    )
    chat_name = _chat_name_from_filename(drive_file.get('name', ''))

    rows = []
    for pm in parsed:
        # Localise the naive exported time to SAST so it round-trips to the exact
        # wall-clock time the team exported (project USE_TZ=True stores UTC).
        sent_at = timezone.make_aware(pm.sent_at, tz)
        rows.append(WhatsAppMessage(
            sender=pm.sender,
            sent_at=sent_at,
            text=pm.text,
            chat_name=chat_name,
            source_file=imported_file,
        ))

    # ignore_conflicts lets the unique (sender, sent_at, text) constraint dedupe
    # messages that also appear in another (e.g. re-exported) file.
    created_objs = WhatsAppMessage.objects.bulk_create(rows, ignore_conflicts=True)
    # With ignore_conflicts, bulk_create's return doesn't reliably carry PKs on
    # all backends; count via the file link instead for an accurate figure.
    created = WhatsAppMessage.objects.filter(source_file=imported_file).count()

    imported_file.message_count = created
    imported_file.save(update_fields=['message_count'])
    return created
