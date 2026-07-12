"""
Google Drive access for the WhatsApp chat import.

Auth mirrors apps/core/tasks.py (OAuth2 refresh token in a pickle), but uses a
SEPARATE credential: the WhatsApp export folder is shared to support@, whereas
the DB-backup task authenticates as info@. So this loads its own token pickle
(WHATSAPP_TOKEN_PICKLE_PATH) and reads its own folder (WHATSAPP_DRIVE_FOLDER_ID).

To create that pickle: run apps/whatsapp_import/generate_support_token.py signed
in as support@alter-power.co.za.

FOLDER LAYOUT (confirmed against the live folder 2026-07-12):
    <top folder>/
        9 July 2026/                              <- one dated subfolder per day
            WhatsApp Chat with House Anderson     <- an application/zip file...
            WhatsApp Chat with House Brown        <- ...with NO ".zip" in its name
            ...                                   <- one zip per conversation
    Each zip contains a single "<chat name>.txt" (the standard WhatsApp export).

So this module walks subfolders recursively and treats files by their Drive
mimeType (application/zip), not by filename extension (the exports have none).
"""

import io
import logging
import os
import pickle
import zipfile

from django.conf import settings
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

logger = logging.getLogger(__name__)

FOLDER_MIME = 'application/vnd.google-apps.folder'
ZIP_MIMES = ('application/zip', 'application/x-zip-compressed', 'multipart/x-zip')
TEXT_MIMES = ('text/plain',)


class DriveConfigError(Exception):
    """Raised when Drive credentials or config are missing/invalid."""


def _load_credentials():
    """Load and refresh the support@ OAuth2 credentials from the token pickle."""
    token_path = getattr(settings, 'WHATSAPP_TOKEN_PICKLE_PATH', None) or os.getenv('WHATSAPP_TOKEN_PICKLE_PATH')
    if not token_path:
        raise DriveConfigError("WHATSAPP_TOKEN_PICKLE_PATH is not set.")
    if not os.path.exists(token_path):
        raise DriveConfigError(f"Token pickle not found at {token_path}. "
                               "Run generate_support_token.py signed in as support@.")

    with open(token_path, 'rb') as fh:
        creds = pickle.load(fh)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        raise DriveConfigError("Invalid WhatsApp Drive credentials — re-run generate_support_token.py as support@.")
    return creds


def get_drive_service():
    """Return an authenticated Drive v3 service for the support@ account."""
    return build('drive', 'v3', credentials=_load_credentials(), cache_discovery=False)


def _folder_id():
    folder_id = getattr(settings, 'WHATSAPP_DRIVE_FOLDER_ID', None) or os.getenv('WHATSAPP_DRIVE_FOLDER_ID')
    if not folder_id:
        raise DriveConfigError("WHATSAPP_DRIVE_FOLDER_ID is not set.")
    return folder_id


def _list_children(service, folder_id):
    """All non-trashed children of a folder (paged). Returns raw Drive dicts."""
    out = []
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            orderBy='modifiedTime desc',
            fields='nextPageToken, files(id, name, mimeType, modifiedTime, size, parents)',
            pageSize=200,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        out.extend(resp.get('files', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return out


def list_export_files(service, folder_id=None, _depth=0, _max_depth=6):
    """
    Recursively walk the shared folder and return every chat-export file found
    at any depth (the exports live inside dated subfolders).

    A "chat-export file" is any file whose mimeType is a zip or plain text — we
    key on mimeType because the exported files have no filename extension. Each
    returned dict also carries `_folder` (the containing folder's name, e.g.
    "9 July 2026") for provenance. `_max_depth` guards against pathological trees.
    """
    if folder_id is None:
        folder_id = _folder_id()

    files = []
    for child in _list_children(service, folder_id):
        mime = child.get('mimeType')
        if mime == FOLDER_MIME:
            if _depth < _max_depth:
                files.extend(list_export_files(service, child['id'], _depth + 1, _max_depth))
            else:
                logger.warning("WhatsApp import: max folder depth reached at %s", child.get('name'))
        elif mime in ZIP_MIMES or mime in TEXT_MIMES:
            files.append(child)
    return files


def _download_bytes(service, file_id):
    """Download a Drive file's raw bytes."""
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def get_chat_text(service, drive_file):
    """
    Return the chat .txt content for an export file, handling both cases:
      - a zip export (application/zip): extract the single .txt inside it, or
      - a bare text file (text/plain): decode directly.

    Decodes UTF-8 leniently so an odd byte can't abort the whole import. Raises
    ValueError if a zip contains no .txt (so the caller logs and skips it).
    """
    raw = _download_bytes(service, drive_file['id'])
    mime = drive_file.get('mimeType', '')

    if mime in ZIP_MIMES:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            txt_names = [n for n in zf.namelist() if n.lower().endswith('.txt')]
            if not txt_names:
                raise ValueError(f"Zip {drive_file.get('name')!r} contains no .txt: {zf.namelist()}")
            # WhatsApp zips contain exactly one chat .txt; if several, prefer the
            # one whose name matches the export (largest as a fallback).
            txt_names.sort(key=lambda n: zf.getinfo(n).file_size, reverse=True)
            return zf.read(txt_names[0]).decode('utf-8', errors='replace')

    # Bare text file.
    return raw.decode('utf-8', errors='replace')
