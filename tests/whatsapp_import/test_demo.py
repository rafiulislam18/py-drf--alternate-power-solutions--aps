"""Demo WhatsApp messages (manage.py seed_whatsapp_demo): add, skip, reset,
clear, and never pushed to the jobs sheet."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.whatsapp_import.demo import DEMO_FILE_ID, MESSAGES
from apps.whatsapp_import.jobs_export import pending_jobs_qs
from apps.whatsapp_import.models import ImportedFile, WhatsAppMessage


def run(*args):
    out = StringIO()
    call_command('seed_whatsapp_demo', *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestSeedWhatsAppDemo:

    def test_adds_all_messages_in_a_mix_of_states(self):
        assert 'Added' in run()
        demo = WhatsAppMessage.objects.filter(source_file__drive_file_id=DEMO_FILE_ID)
        assert demo.count() == len(MESSAGES)
        assert demo.filter(marked_as_job=False, dismissed=False).exists()
        assert demo.filter(marked_as_job=True).exists() and demo.filter(dismissed=True).exists()
        assert demo.values('chat_name').distinct().count() >= 3
        assert not demo.filter(sent_at__gt=timezone.now()).exists()
        assert demo.filter(sent_at__gte=timezone.now() - timedelta(days=1)).exists()

    def test_second_run_skips(self):
        run()
        assert 'already there' in run()
        assert WhatsAppMessage.objects.count() == len(MESSAGES)

    def test_reset_and_clear(self):
        run()
        WhatsAppMessage.objects.filter(source_file__drive_file_id=DEMO_FILE_ID).update(dismissed=True)
        run('--reset')
        assert WhatsAppMessage.objects.filter(dismissed=False).exists()
        assert WhatsAppMessage.objects.count() == len(MESSAGES)
        assert f'Removed {len(MESSAGES)}' in run('--clear')
        assert not WhatsAppMessage.objects.exists()
        assert not ImportedFile.objects.filter(drive_file_id=DEMO_FILE_ID).exists()

    def test_demo_messages_never_reach_the_jobs_sheet(self):
        run()
        # The ops manager ticks every demo message as a job during a demo...
        WhatsAppMessage.objects.update(marked_as_job=True, dismissed=False, exported_to_jobs_sheet=False)
        assert pending_jobs_qs().count() == 0
        # ...while a real message still exports as normal.
        real = WhatsAppMessage.objects.create(sender='Real', sent_at=timezone.now(), text='Real job',
                                              marked_as_job=True)
        assert list(pending_jobs_qs()) == [real]

    def test_clear_leaves_real_messages(self):
        real = WhatsAppMessage.objects.create(sender='Real', sent_at=timezone.now(), text='Keep me')
        run()
        run('--clear')
        assert list(WhatsAppMessage.objects.all()) == [real]
