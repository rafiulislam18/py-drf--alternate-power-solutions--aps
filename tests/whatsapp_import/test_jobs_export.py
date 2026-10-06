"""
Tests for the jobs-sheet export (apps.whatsapp_import.jobs_export + views).

The Apps Script POST is always mocked — no real network. Covers the field
mapping, the idempotent flag-flip (only what the sheet confirms), failure
leaving messages pending, config guard, the one-export-at-a-time lock, and the
admin-only push endpoint.
"""

from datetime import datetime, timezone as dt_timezone
from unittest import mock

import pytest

from apps.core.models import ClientProfile
from django.core.cache import cache

from apps.whatsapp_import import jobs_export
from apps.whatsapp_import.models import WhatsAppMessage
from apps.whatsapp_import.tasks import export_jobs_to_sheet


@pytest.fixture(autouse=True)
def _clear_export_lock():
    """The SQLite fallback lock lives in the (process-wide) locmem cache."""
    cache.delete(jobs_export._CACHE_LOCK_KEY)
    yield
    cache.delete(jobs_export._CACHE_LOCK_KEY)


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

    def test_payload_includes_message_id(self, sheet_settings):
        m = _msg(marked_as_job=True)
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        return_value=_ok_response(1)) as post:
            jobs_export.export_marked_jobs()
        assert post.call_args.kwargs['json']['jobs'][0]['message_id'] == m.pk

    def test_dismissed_marked_message_not_pending(self, sheet_settings):
        _msg(marked_as_job=True, dismissed=True, text='not a job')
        keep = _msg(marked_as_job=True, text='real job')
        assert list(jobs_export.pending_jobs_qs()) == [keep]
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        return_value=_ok_response(1)) as post:
            stats = jobs_export.export_marked_jobs()
        assert stats['found'] == 1
        assert [j['message_id'] for j in post.call_args.kwargs['json']['jobs']] == [keep.pk]

    def test_unmarked_during_export_not_flagged(self, sheet_settings):
        a = _msg(marked_as_job=True, text='a')
        b = _msg(marked_as_job=True, text='b')

        def unmark_mid_flight(*args, **kwargs):
            # Ops manager unmarks b while the POST is in flight.
            WhatsAppMessage.objects.filter(pk=b.pk).update(marked_as_job=False)
            return _ok_response(2)

        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        side_effect=unmark_mid_flight):
            stats = jobs_export.export_marked_jobs()
        a.refresh_from_db(); b.refresh_from_db()
        assert a.exported_to_jobs_sheet is True
        assert b.exported_to_jobs_sheet is False
        assert stats['exported'] == 1 and stats['unmarked_during_export'] == 1

    def test_sheet_message_ids_flag_exactly_those(self, sheet_settings):
        a = _msg(marked_as_job=True, text='a')
        _msg(marked_as_job=True, text='b')
        c = _msg(marked_as_job=True, text='c')
        resp = mock.MagicMock()
        # Sheet deduped a (already present) and created c; b failed.
        resp.json.return_value = {'ok': True, 'created': 1, 'message_ids': [a.pk, c.pk]}
        resp.raise_for_status.return_value = None
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post', return_value=resp):
            stats = jobs_export.export_marked_jobs()
        flagged = set(WhatsAppMessage.objects.filter(exported_to_jobs_sheet=True)
                      .values_list('pk', flat=True))
        assert flagged == {a.pk, c.pk}
        assert stats['exported'] == 2 and stats['skipped'] == 1

    def test_nothing_pending(self, sheet_settings):
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post') as post:
            stats = jobs_export.export_marked_jobs()
        assert stats['found'] == 0
        post.assert_not_called()


@pytest.mark.django_db
class TestExportLock:

    def test_second_export_refused_while_running(self, sheet_settings):
        _msg(marked_as_job=True)

        def nested_export(*args, **kwargs):
            # A second export (e.g. the nightly task) starts mid-POST.
            with pytest.raises(jobs_export.ExportInProgress):
                jobs_export.export_marked_jobs()
            return _ok_response(1)

        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        side_effect=nested_export) as post:
            stats = jobs_export.export_marked_jobs()
        assert stats['exported'] == 1
        assert post.call_count == 1

    def test_lock_released_after_failure(self, sheet_settings):
        _msg(marked_as_job=True)
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        side_effect=Exception('network down')):
            jobs_export.export_marked_jobs()
        # Lock was released, so the next run goes ahead and succeeds.
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post',
                        return_value=_ok_response(1)):
            assert jobs_export.export_marked_jobs()['exported'] == 1

    def test_push_returns_409_when_locked(self, sheet_settings, admin_api_client):
        _msg(marked_as_job=True)
        cache.add(jobs_export._CACHE_LOCK_KEY, 'someone-else', 60)
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post') as post:
            res = admin_api_client.post('/whatsapp/jobs-export/')
        assert res.status_code == 409
        assert 'already running' in res.data['detail']
        post.assert_not_called()
        assert WhatsAppMessage.objects.filter(exported_to_jobs_sheet=False).count() == 1

    def test_nightly_task_skips_when_locked(self, sheet_settings):
        _msg(marked_as_job=True)
        cache.add(jobs_export._CACHE_LOCK_KEY, 'someone-else', 60)
        with mock.patch('apps.whatsapp_import.jobs_export.requests.post') as post:
            result = export_jobs_to_sheet.apply().get()
        assert result == 'skipped: export already running'
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
