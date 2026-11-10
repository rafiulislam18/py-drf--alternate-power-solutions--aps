"""manage.py delete_all_jobs: wipe jobs (all, or only imported ones) after a wrong import."""

from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.jobs.models import Job, JobCategory, JobComment
from apps.jobs.services import save_with_next_number
from apps.records.models import AuditLog
from apps.whatsapp_import.jobs_export import export_marked_jobs
from apps.whatsapp_import.models import WhatsAppMessage

pytestmark = pytest.mark.django_db


def make_job(imported=False, trashed=False):
    job = save_with_next_number(Job(category=JobCategory.objects.first(), client_name='X',
                                    imported_from_sheet=imported))
    JobComment.objects.create(job=job, author_name='Operations sheet', body='note')
    AuditLog.record(None, AuditLog.CREATED, job)
    if trashed:
        job.trash(None)
    return job


def test_asks_first_and_deletes_everything(settings):
    settings.DEBUG = True
    make_job(imported=True)
    make_job(trashed=True)
    with mock.patch('builtins.input', return_value='no'), pytest.raises(CommandError, match='Nothing was deleted'):
        call_command('delete_all_jobs')
    assert Job.all_objects.count() == 2

    with mock.patch('builtins.input', return_value='Delete all jobs'):
        call_command('delete_all_jobs')
    assert not Job.all_objects.exists() and not JobComment.objects.exists()
    assert not AuditLog.objects.filter(object_type='jobs.job').exists()  # local: history goes too
    assert make_job().job_id == 'APS-001'  # numbers free again


def test_imported_only_and_whatsapp_messages_unlocked(settings):
    settings.DEBUG = False
    WhatsAppMessage.objects.create(sender='Brian', text='fix gate', chat_name='Rialto', marked_as_job=True,
                                   sent_at=timezone.now())
    export_marked_jobs()
    typed = Job.objects.get()
    imported = make_job(imported=True)

    call_command('delete_all_jobs', '--imported-only', '--yes')
    assert list(Job.all_objects.values_list('pk', flat=True)) == [typed.pk]
    # Live: history can't be deleted — it stays, plus one entry for the deletion.
    assert AuditLog.objects.filter(object_type='jobs.job', object_id=str(imported.pk)).exists()
    assert AuditLog.objects.filter(action='removed', summary='1 jobs deleted with manage.py delete_all_jobs').exists()

    call_command('delete_all_jobs', '--yes')
    message = WhatsAppMessage.objects.get()
    assert message.job is None and not message.exported_to_jobs_sheet  # can become a job again
    assert export_marked_jobs()['exported'] == 1
