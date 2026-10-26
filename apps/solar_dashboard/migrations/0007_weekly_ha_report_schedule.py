"""
Register the weekly Home Assistant solar report as a Celery Beat periodic task.

Runs every Monday at 01:00 in the project's Celery timezone (Africa/Johannesburg /
SAST — see CELERY_TIMEZONE in settings), an hour after the week ends so HA has
finished compiling Sunday's last hourly statistics. Applied automatically during
`migrate` on deploy. Idempotent and reversible.
"""

from django.conf import settings
from django.db import migrations

TASK_PATH = 'apps.solar_dashboard.tasks.create_weekly_ha_report'
TASK_NAME = 'Weekly HA Solar Report (Mondays 01:00 SAST)'


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    tz = getattr(settings, 'CELERY_TIMEZONE', 'Africa/Johannesburg')
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='0',
        hour='1',
        day_of_week='1',  # Monday (crontab: 0 = Sunday)
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
        ('solar_dashboard', '0006_site_address_created_by'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
