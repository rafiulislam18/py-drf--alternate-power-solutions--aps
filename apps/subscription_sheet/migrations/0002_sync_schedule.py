"""
Register the nightly subscriptions-sheet sync as a Celery Beat periodic task.

Runs at 01:00 SAST (after the WhatsApp import 00:00 and jobs export 00:30).
Idempotent and reversible; applied automatically on `migrate`.
"""

from django.conf import settings
from django.db import migrations

TASK_PATH = 'apps.subscription_sheet.tasks.sync_subscriptions_to_sheet'
TASK_NAME = 'APS Subscriptions Sheet Sync (nightly 01:00 SAST)'


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    tz = getattr(settings, 'CELERY_TIMEZONE', 'Africa/Johannesburg')
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='0',
        hour='1',
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
        ('subscription_sheet', '0001_initial'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
