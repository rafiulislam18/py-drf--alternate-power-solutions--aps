"""
Register the new-ticket email job as a Celery Beat periodic task.

Every 30 minutes (on the hour and half-hour, SAST) it emails the team about
normal/urgent tickets raised since the last run, and sends each client their
confirmation — see apps.client_portal.alerts. Emergency tickets don't wait for
it. Idempotent and reversible; applied automatically on `migrate`. Needs a
running Celery worker and beat, like the other scheduled jobs.
"""

from django.conf import settings
from django.db import migrations

TASK_PATH = 'apps.client_portal.tasks.send_new_ticket_alerts'
TASK_NAME = 'Tickets: new-ticket emails (every 30 min)'


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    tz = getattr(settings, 'CELERY_TIMEZONE', 'Africa/Johannesburg')
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='0,30',
        hour='*',
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
        ('client_portal', '0011_ticket_alert_pending'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
