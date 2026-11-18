"""
Register the daily overdue-tools digest (apps.tools.tasks.send_overdue_digest)
as a Celery Beat periodic task at 09:00 SAST, the sheet's overdue-tools time.
Switching "Overdue emails" off in Tool settings pauses it without touching
this schedule. Idempotent and reversible; applied automatically on `migrate`.
"""

from django.conf import settings
from django.db import migrations

TASK_PATH = 'apps.tools.tasks.send_overdue_digest'
TASK_NAME = 'APS Overdue Tools Digest (daily 09:00 SAST)'


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    tz = getattr(settings, 'CELERY_TIMEZONE', 'Africa/Johannesburg')
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='0',
        hour='9',
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
        ('tools', '0002_seed_types'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
