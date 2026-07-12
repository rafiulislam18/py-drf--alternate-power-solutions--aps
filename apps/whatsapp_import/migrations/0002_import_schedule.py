"""
Register the nightly WhatsApp import as a Celery Beat periodic task.

Runs at 00:00 every day in the project's Celery timezone (Africa/Johannesburg /
SAST — see CELERY_TIMEZONE in settings). Applied automatically during `migrate`
on deploy, so production gets the schedule without a manual step. Idempotent and
reversible.
"""

from django.conf import settings
from django.db import migrations

TASK_PATH = 'apps.whatsapp_import.tasks.import_whatsapp_chats'
TASK_NAME = 'WhatsApp Chat Import (nightly 00:00 SAST)'


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    # Beat interprets this crontab in the schedule's own timezone. CELERY_TIMEZONE
    # is Africa/Johannesburg, so 00:00 here is midnight Cape Town time.
    tz = getattr(settings, 'CELERY_TIMEZONE', 'Africa/Johannesburg')
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='0',
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
        ('whatsapp_import', '0001_initial'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
