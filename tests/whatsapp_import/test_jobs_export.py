"""
Tests for the jobs-sheet export (apps.whatsapp_import.jobs_export + views).

The Apps Script POST is always mocked — no real network. Covers the field
mapping, the idempotent flag-flip (only what the sheet confirms), failure
leaving messages pending, config guard, and the admin-only push endpoint.
"""

from datetime import datetime, timezone as dt_timezone
from unittest import mock

import pytest

from apps.core.models import ClientProfile
from apps.whatsapp_import import jobs_export
from apps.whatsapp_import.models import WhatsAppMessage


@pytest.fixture
def sheet_settings(settings):
    """Configure the jobs-sheet URL/token for a test."""
    settings.WHATSAPP_JOBS_SHEET_URL = 'https://script.google/exec'
    settings.WHATSAPP_JOBS_SHEET_TOKEN = 'secret'
    return settings


def _msg(**kw):
    defaults = dict(
        sender='Brian', text='need a new inverter', chat_name='House Brown',
        sent_at=datetime(2026, 7, 10, 8, 0, tzinfo=dt_timezone.utc),
    )
    defaults.update(kw)
    return WhatsAppMessage.objects.create(**defaults)


def _ok_response(created):
    m = mock.MagicMock()
    m.json.return_value = {'ok': True, 'created': created, 'ids': [f'APS-{i:03d}' for i in range(created)]}
    m.raise_for_status.return_value = None
    return m


@pytest.mark.django_db
class TestExportService:

    def test_pushes_and_flags_exported(self, sheet_settings):
        _msg(marked_as_job=True)
        _msg(marked_as_job=True, text='second job')
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        return_value=_ok_response(2)) as post:
            stats = jobs_export.export_marked_jobs()
        assert stats['found'] == 2 and stats['exported'] == 2
        assert WhatsAppMessage.objects.filter(exported_to_jobs_sheet=True).count() == 2
        # Payload field mapping: chat_name->client, text->task.
        sent = post.call_args.kwargs['json']
        assert sent['token'] == 'secret'
        assert sent['jobs'][0]['client'] == 'House Brown'
        assert sent['jobs'][0]['task'] in ('need a new inverter', 'second job')

    def test_only_marked_unexported_are_sent(self, sheet_settings):
        _msg(marked_as_job=True, text='eligible one')                              # eligible
        _msg(marked_as_job=False, text='not marked')                              # not marked
        _msg(marked_as_job=True, exported_to_jobs_sheet=True, text='already done')  # already exported
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        return_value=_ok_response(1)):
            stats = jobs_export.export_marked_jobs()
        assert stats['found'] == 1 and stats['exported'] == 1

    def test_failed_post_leaves_messages_pending(self, sheet_settings):
        _msg(marked_as_job=True)
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        side_effect=Exception('network down')):
            stats = jobs_export.export_marked_jobs()
        assert stats['error'] is not None
        assert stats['exported'] == 0
        assert WhatsAppMessage.objects.filter(exported_to_jobs_sheet=False).count() == 1

    def test_sheet_rejection_leaves_pending(self, sheet_settings):
        _msg(marked_as_job=True)
        bad = mock.MagicMock()
        bad.json.return_value = {'ok': False, 'error': 'unauthorized'}
        bad.raise_for_status.return_value = None
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post', return_value=bad):
            stats = jobs_export.export_marked_jobs()
        assert stats['error'] == 'unauthorized'
        assert not WhatsAppMessage.objects.filter(exported_to_jobs_sheet=True).exists()

    def test_partial_created_flags_only_confirmed(self, sheet_settings):
        _msg(marked_as_job=True)
        _msg(marked_as_job=True, text='b')
        _msg(marked_as_job=True, text='c')
        # Sheet says it only created 2 of the 3.
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        return_value=_ok_response(2)):
            stats = jobs_export.export_marked_jobs()
        assert stats['exported'] == 2 and stats['skipped'] == 1
        assert WhatsAppMessage.objects.filter(exported_to_jobs_sheet=True).count() == 2

    def test_nothing_pending(self, sheet_settings):
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post') as post:
            stats = jobs_export.export_marked_jobs()
        assert stats['found'] == 0
        post.assert_not_called()


@pytest.mark.django_db
class TestExportConfigGuard:
    def test_missing_config_raises(self, settings):
        settings.WHATSAPP_JOBS_SHEET_URL = ''
        settings.WHATSAPP_JOBS_SHEET_TOKEN = ''
        _msg(marked_as_job=True)
        with pytest.raises(jobs_export.JobsSheetConfigError):
            jobs_export.export_marked_jobs()


@pytest.mark.django_db
class TestExportEndpoint:

    def test_pending_count(self, sheet_settings, admin_api_client):
        _msg(marked_as_job=True)
        _msg(marked_as_job=True, text='b')
        res = admin_api_client.get('/whatsapp/jobs-export/')
        assert res.status_code == 200 and res.data['pending'] == 2

    def test_push_button(self, sheet_settings, admin_api_client):
        _msg(marked_as_job=True)
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        return_value=_ok_response(1)):
            res = admin_api_client.post('/whatsapp/jobs-export/')
        assert res.status_code == 200
        assert res.data['exported'] == 1

    def test_push_forbidden_for_client(self, sheet_settings, api_client, user_factory):
        u = user_factory(username='c1')
        ClientProfile.objects.create(user=u, role='client', company_name='X')
        api_client.force_authenticate(user=u)
        assert api_client.post('/whatsapp/jobs-export/').status_code == 403

    def test_push_requires_auth(self, sheet_settings, api_client):
        assert api_client.post('/whatsapp/jobs-export/').status_code == 401
