"""
Tests for the WhatsApp chat import (apps.whatsapp_import).

Covers the format-tolerant parser across several export variants, the SAST
timestamp handling, and the ingestion service's file-level dedup + message-level
dedup with a mocked Drive service.
"""

from datetime import datetime
from unittest import mock

import pytest

from apps.whatsapp_import.models import ImportedFile, WhatsAppMessage
from apps.whatsapp_import.parser import parse_chat_export
from apps.whatsapp_import import services


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class TestParser:

    def test_bracketed_24h_with_seconds(self):
        text = "[2024/03/12, 14:32:05] Thabo: Hi, is the inverter available?"
        msgs = parse_chat_export(text)
        assert len(msgs) == 1
        assert msgs[0].sender == 'Thabo'
        assert msgs[0].sent_at == datetime(2024, 3, 12, 14, 32, 5)
        assert msgs[0].text == 'Hi, is the inverter available?'

    def test_dash_separator_24h(self):
        text = "2024/03/12, 14:32 - Thabo: Yes in stock"
        msgs = parse_chat_export(text)
        assert len(msgs) == 1
        assert msgs[0].sender == 'Thabo'
        assert msgs[0].sent_at == datetime(2024, 3, 12, 14, 32)

    def test_us_12h_ampm(self):
        text = "3/12/24, 2:32 PM - Thabo: afternoon message"
        msgs = parse_chat_export(text)
        assert len(msgs) == 1
        assert msgs[0].sent_at == datetime(2024, 3, 12, 14, 32)

    def test_multiline_message_is_stitched(self):
        text = (
            "[2024/03/12, 14:32:05] Thabo: line one\n"
            "line two\n"
            "line three\n"
            "[2024/03/12, 14:35:00] APS: reply"
        )
        msgs = parse_chat_export(text)
        assert len(msgs) == 2
        assert msgs[0].text == 'line one\nline two\nline three'
        assert msgs[1].sender == 'APS'

    def test_system_messages_skipped_by_default(self):
        text = (
            "[2024/03/12, 00:00:00] Messages and calls are end-to-end encrypted.\n"
            "[2024/03/12, 14:32:05] Thabo: real message"
        )
        msgs = parse_chat_export(text)
        assert len(msgs) == 1
        assert msgs[0].sender == 'Thabo'

    def test_system_messages_included_when_requested(self):
        text = "[2024/03/12, 00:00:00] Messages and calls are end-to-end encrypted."
        msgs = parse_chat_export(text, include_system=True)
        assert len(msgs) == 1
        assert msgs[0].sender == ''

    def test_invisible_marks_stripped(self):
        # WhatsApp injects a LTR mark (U+200E) right after the bracket.
        text = "[2024/03/12, 14:32:05] ‎Thabo: hi"
        msgs = parse_chat_export(text)
        assert len(msgs) == 1
        assert msgs[0].sender == 'Thabo'

    def test_malformed_line_does_not_crash(self):
        text = (
            "totally not a whatsapp line\n"
            "[2024/03/12, 14:32:05] Thabo: valid\n"
        )
        msgs = parse_chat_export(text)
        # Leading noise before the first header is ignored; valid msg still parsed.
        assert len(msgs) == 1
        assert msgs[0].text == 'valid'

    def test_empty_input(self):
        assert parse_chat_export('') == []


# ---------------------------------------------------------------------------
# Ingestion service (mocked Drive)
# ---------------------------------------------------------------------------

SAMPLE = (
    "[2024/03/12, 14:32:05] Thabo: need a quote for solar\n"
    "[2024/03/12, 14:35:00] APS: sure, sending now"
)


@pytest.mark.django_db
class TestIngestion:

    def _run(self, files, contents, limit=None):
        """`files` are Drive dicts; `contents` maps file id -> chat text.
        get_chat_text(service, drive_file) is mocked to return that text."""
        svc = mock.MagicMock()
        with mock.patch('apps.whatsapp_import.services.list_export_files', return_value=files), \
             mock.patch('apps.whatsapp_import.services.get_chat_text',
                        side_effect=lambda service, drive_file: contents[drive_file['id']]):
            return services.import_new_files(service=svc, limit=limit)

    def test_imports_new_file_and_creates_rows(self):
        files = [{'id': 'f1', 'name': 'WhatsApp Chat with Thabo.txt', 'modifiedTime': '2024-03-12T20:00:00Z'}]
        stats = self._run(files, {'f1': SAMPLE})
        assert stats['files_imported'] == 1
        assert stats['messages_created'] == 2
        assert WhatsAppMessage.objects.count() == 2
        # Provenance + chat name derived from filename.
        m = WhatsAppMessage.objects.order_by('sent_at').first()
        assert m.chat_name == 'Thabo'
        assert m.source_file.drive_file_id == 'f1'

    def test_timestamp_stored_as_sast(self):
        files = [{'id': 'f1', 'name': 'chat.txt', 'modifiedTime': ''}]
        self._run(files, {'f1': "[2024/03/12, 14:32:05] Thabo: hi"})
        m = WhatsAppMessage.objects.get()
        # Stored aware; its SAST wall-clock must equal the exported 14:32:05.
        from zoneinfo import ZoneInfo
        local = m.sent_at.astimezone(ZoneInfo('Africa/Johannesburg'))
        assert (local.hour, local.minute, local.second) == (14, 32, 5)
        # And it is +02:00 offset (SAST), i.e. 12:32:05 UTC.
        assert m.sent_at.astimezone(ZoneInfo('UTC')).hour == 12

    def test_already_imported_file_skipped(self):
        files = [{'id': 'f1', 'name': 'chat.txt', 'modifiedTime': ''}]
        self._run(files, {'f1': SAMPLE})
        # Second run over the same file: nothing new.
        stats = self._run(files, {'f1': SAMPLE})
        assert stats['files_new'] == 0
        assert stats['files_imported'] == 0
        assert WhatsAppMessage.objects.count() == 2

    def test_duplicate_message_across_files_deduped(self):
        f1 = [{'id': 'f1', 'name': 'a.txt', 'modifiedTime': ''}]
        self._run(f1, {'f1': SAMPLE})
        # A different file that repeats one identical message + one new one.
        f2 = [{'id': 'f2', 'name': 'b.txt', 'modifiedTime': ''}]
        overlap = SAMPLE + "\n[2024/03/12, 15:00:00] Thabo: extra"
        stats = self._run(f2, {'f2': overlap})
        assert stats['files_imported'] == 1
        # Only the genuinely new message survives the unique constraint.
        assert WhatsAppMessage.objects.count() == 3

    def test_bad_file_does_not_abort_run(self):
        files = [
            {'id': 'ok', 'name': 'good.txt', 'modifiedTime': ''},
            {'id': 'bad', 'name': 'bad.txt', 'modifiedTime': ''},
        ]

        def fetch(service, drive_file):
            if drive_file['id'] == 'bad':
                raise RuntimeError('drive exploded')
            return SAMPLE

        svc = mock.MagicMock()
        with mock.patch('apps.whatsapp_import.services.list_export_files', return_value=files), \
             mock.patch('apps.whatsapp_import.services.get_chat_text', side_effect=fetch):
            stats = services.import_new_files(service=svc)

        assert stats['files_imported'] == 1
        assert len(stats['errors']) == 1
        assert stats['errors'][0]['id'] == 'bad'
        # The good file's rows are committed despite the bad one failing.
        assert WhatsAppMessage.objects.count() == 2

    def test_limit_restricts_new_files(self):
        files = [
            {'id': 'f1', 'name': 'a.txt', 'modifiedTime': ''},
            {'id': 'f2', 'name': 'b.txt', 'modifiedTime': ''},
        ]
        stats = self._run(files, {'f1': SAMPLE, 'f2': "[2024/03/13, 09:00:00] X: y"}, limit=1)
        assert stats['files_new'] == 1
        assert ImportedFile.objects.count() == 1

    def test_real_group_chat_format(self):
        """The live format: 'YYYY/MM/DD, HH:MM - Sender: text', group chat, with
        system lines and a 'null' sender — matches the actual exported files."""
        real = (
            "2024/11/20, 13:32 - Messages and calls are end-to-end encrypted.\n"
            "2024/10/17, 14:51 - Faiza created group \"House Anderson\"\n"
            "2024/11/20, 16:52 - Crystal Lippert: please note at the site meeting\n"
            "discussions about the fan.\n"
            "2024/11/20, 16:59 - null: Don't forget the inverter"
        )
        files = [{'id': 'z1', 'name': 'WhatsApp Chat with House Anderson', 'modifiedTime': ''}]
        stats = self._run(files, {'z1': real})
        # Two real messages; the two system lines are skipped.
        assert stats['messages_created'] == 2
        m = WhatsAppMessage.objects.order_by('sent_at')
        assert list(m.values_list('sender', flat=True)) == ['Crystal Lippert', 'null']
        # Extension-less filename -> clean chat name.
        assert m.first().chat_name == 'House Anderson'
        assert m.first().text == 'please note at the site meeting\ndiscussions about the fan.'


class TestGetChatText:
    """drive.get_chat_text handles both a bare text file and a zip (the real case)."""

    def test_extracts_txt_from_zip(self):
        import io
        import zipfile
        from apps.whatsapp_import import drive

        payload = "2024/11/20, 16:52 - Crystal: hello from inside a zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('WhatsApp Chat with House Anderson.txt', payload)
        zip_bytes = buf.getvalue()

        drive_file = {'id': 'z1', 'name': 'WhatsApp Chat with House Anderson',
                      'mimeType': 'application/zip'}
        with mock.patch('apps.whatsapp_import.drive._download_bytes', return_value=zip_bytes):
            text = drive.get_chat_text(mock.MagicMock(), drive_file)
        assert text == payload

    def test_bare_text_file(self):
        from apps.whatsapp_import import drive
        drive_file = {'id': 't1', 'name': 'chat.txt', 'mimeType': 'text/plain'}
        with mock.patch('apps.whatsapp_import.drive._download_bytes',
                        return_value='2024/11/20, 16:52 - A: hi'.encode('utf-8')):
            text = drive.get_chat_text(mock.MagicMock(), drive_file)
        assert 'hi' in text

    def test_zip_without_txt_raises(self):
        import io
        import zipfile
        from apps.whatsapp_import import drive

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('IMG-001.jpg', b'\xff\xd8\xff')
        with mock.patch('apps.whatsapp_import.drive._download_bytes', return_value=buf.getvalue()):
            with pytest.raises(ValueError):
                drive.get_chat_text(mock.MagicMock(),
                                    {'id': 'z', 'name': 'x', 'mimeType': 'application/zip'})
