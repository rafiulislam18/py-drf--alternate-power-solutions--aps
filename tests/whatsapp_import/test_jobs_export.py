"""
Marked WhatsApp messages → dashboard jobs (apps.whatsapp_import.jobs_export +
the "Create jobs" endpoint + the nightly task).

Covers the field mapping, one job per message (never twice), messages
unmarked or dismissed meanwhile, the one-run-at-a-time lock, and permissions.
Nothing goes to the Operations sheet any more.
"""

import importlib
from datetime import datetime, timezone as dt_timezone
from unittest import mock

import pytest
from django.core.cache import cache

from apps.core.models import ClientProfile
from apps.jobs.models import Job
from apps.records.models import AuditLog
from apps.whatsapp_import import jobs_export
from apps.whatsapp_import.models import WhatsAppMessage
from apps.whatsapp_import.tasks import create_jobs_from_whatsapp


@pytest.fixture(autouse=True)
def _setup(db):
    """The SQLite fallback lock lives in the (process-wide) locmem cache; tests skip
    migrations, so seed the job categories."""
    from django.apps import apps
    importlib.import_module('apps.jobs.migrations.0002_seed_categories').seed(apps, None)
    cache.delete(jobs_export._CACHE_LOCK_KEY)
    yield
    cache.delete(jobs_export._CACHE_LOCK_KEY)


def _msg(**kw):
    defaults = dict(
        sender='Brian', text='need a new inverter', chat_name='House Brown',
        sent_at=datetime(2026, 7, 10, 8, 0, tzinfo=dt_timezone.utc),
    )
    defaults.update(kw)
    return WhatsAppMessage.objects.create(**defaults)


@pytest.mark.django_db
class TestCreateJobs:

    def test_each_marked_message_becomes_a_job(self, admin_user):
        first = _msg(marked_as_job=True)
        second = _msg(marked_as_job=True, text='second job', chat_name='', sender='Sipho')
        stats = jobs_export.export_marked_jobs(user=admin_user)
        assert (stats['found'], stats['exported'], stats['job_ids']) == (2, 2, ['APS-001', 'APS-002'])

        job = Job.objects.select_related('category').get(number=1)
        assert (job.client_name, job.task, job.category.name, job.status) == \
            ('House Brown', 'need a new inverter', 'Open jobs', 'open')
        assert job.created_by == admin_user and job.date_added and job.last_updated
        comment = job.comments.get()
        assert comment.author_name == 'WhatsApp' and comment.body == 'From WhatsApp — Brian (10 Jul 2026 10:00)'
        assert Job.objects.get(number=2).client_name == 'Sipho'  # no chat name → the sender

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.exported_to_jobs_sheet and first.exported_to_jobs_sheet_at and first.job == job
        assert AuditLog.objects.filter(object_type='jobs.job', action='created',
                                       summary='From WhatsApp (House Brown)').exists()

        # Never twice.
        assert jobs_export.export_marked_jobs()['found'] == 0
        assert Job.objects.count() == 2

    def test_only_marked_not_dismissed_not_sent(self):
        _msg(marked_as_job=True)
        _msg(marked_as_job=False, text='not a job')
        _msg(marked_as_job=True, dismissed=True, text='dismissed')
        _msg(marked_as_job=True, exported_to_jobs_sheet=True, text='sent to the sheet before')
        stats = jobs_export.export_marked_jobs()
        assert (stats['found'], stats['exported']) == (1, 1)
        assert list(Job.objects.values_list('task', flat=True)) == ['need a new inverter']

    def test_unmarked_meanwhile_is_skipped(self):
        keep, gone = _msg(marked_as_job=True), _msg(marked_as_job=True, text='changed mind')
        real_qs = jobs_export.pending_jobs_qs

        def listed_then_unmarked():
            qs = real_qs()
            ids = list(qs.values_list('pk', flat=True))
            WhatsAppMessage.objects.filter(pk=gone.pk).update(marked_as_job=False)
            return real_qs().model.objects.filter(pk__in=ids).order_by('sent_at')

        with mock.patch.object(jobs_export, 'pending_jobs_qs', listed_then_unmarked):
            stats = jobs_export.export_marked_jobs()
        assert (stats['found'], stats['exported']) == (2, 1)
        keep.refresh_from_db()
        gone.refresh_from_db()
        assert keep.job is not None and gone.job is None and not gone.exported_to_jobs_sheet

    def test_numbers_follow_existing_jobs(self, admin_api_client):
        from apps.jobs.models import JobCategory
        admin_api_client.post('/jobs/', {'client_name': 'Typed in', 'category': JobCategory.objects.first().pk},
                              format='json')
        _msg(marked_as_job=True)
        assert jobs_export.export_marked_jobs()['job_ids'] == ['APS-002']

    def test_nothing_pending(self):
        stats = jobs_export.export_marked_jobs()
        assert stats == {'found': 0, 'exported': 0, 'job_ids': []}


@pytest.mark.django_db
class TestLock:

    def test_second_run_refused_while_running(self):
        _msg(marked_as_job=True)
        cache.add(jobs_export._CACHE_LOCK_KEY, 'someone-else', 60)
        with pytest.raises(jobs_export.ExportInProgress):
            jobs_export.export_marked_jobs()
        assert not Job.objects.exists()

    def test_button_returns_409_when_locked(self, admin_api_client):
        _msg(marked_as_job=True)
        cache.add(jobs_export._CACHE_LOCK_KEY, 'someone-else', 60)
        res = admin_api_client.post('/whatsapp/jobs-export/')
        assert res.status_code == 409 and 'already being created' in res.data['detail']
        assert WhatsAppMessage.objects.filter(exported_to_jobs_sheet=False).count() == 1

    def test_nightly_task(self):
        _msg(marked_as_job=True)
        cache.add(jobs_export._CACHE_LOCK_KEY, 'someone-else', 60)
        assert create_jobs_from_whatsapp.apply().get() == 'skipped: already running'
        cache.delete(jobs_export._CACHE_LOCK_KEY)
        assert create_jobs_from_whatsapp.apply().get() == 'WhatsApp → jobs: 1 created, 1 found.'
        assert Job.objects.get().created_by is None  # made by the system


@pytest.mark.django_db
class TestEndpoint:

    def test_pending_count(self, admin_api_client):
        _msg(marked_as_job=True)
        _msg(marked_as_job=True, text='b')
        res = admin_api_client.get('/whatsapp/jobs-export/')
        assert res.status_code == 200 and res.data['pending'] == 2

    def test_create_jobs_button_and_message_link(self, admin_api_client):
        _msg(marked_as_job=True)
        res = admin_api_client.post('/whatsapp/jobs-export/')
        assert res.status_code == 200 and res.data['exported'] == 1 and res.data['job_ids'] == ['APS-001']
        listed = admin_api_client.get('/whatsapp/messages/', {'status': 'marked'}).data['results'][0]
        assert listed['job'] == {'id': Job.objects.get().pk, 'job_id': 'APS-001'}
        assert AuditLog.objects.filter(action='exported', summary='1 job(s): APS-001').exists()

    def test_forbidden_for_client(self, api_client, user_factory):
        u = user_factory(username='c1')
        ClientProfile.objects.create(user=u, role='client', company_name='X')
        api_client.force_authenticate(user=u)
        assert api_client.post('/whatsapp/jobs-export/').status_code == 403

    def test_requires_auth(self, api_client):
        assert api_client.post('/whatsapp/jobs-export/').status_code == 401
