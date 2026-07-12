"""
Register the nightly jobs-sheet export as a Celery Beat periodic task.

Runs at 00:30 SAST — half an hour after the import (00:00) so a fresh import is
already stored before we sweep marked-as-job messages to the sheet. Idempotent
and reversible; applied automatically on `migrate`.
"""

from django.conf import settings
from django.db import migrations

TASK_PATH = 'apps.whatsapp_import.tasks.export_jobs_to_sheet'
TASK_NAME = 'WhatsApp Jobs Export (nightly 00:30 SAST safety-net)'


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    tz = getattr(settings, 'CELERY_TIMEZONE', 'Africa/Johannesburg')
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='30',
        hour='0',
        day_of_week='*',
        day_of_month='*',
        month_of_year='*',
        timezone=tz,
    )
    PeriodicTask.objects.update_or_create(
        task=TASK_PATH,
        defaults={
            'name': TASK_NAME,
            'crontab': schedule,
            'interval': None,
            'enabled': True,
        },
    )


def remove_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(task=TASK_PATH).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_import', '0002_import_schedule'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
