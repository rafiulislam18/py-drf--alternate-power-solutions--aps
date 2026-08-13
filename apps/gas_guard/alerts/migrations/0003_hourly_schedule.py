"""
Register the hourly low-gas check as a Celery Beat periodic task.

Runs automatically during `migrate`, so any environment gets the schedule
without a manual step. Idempotent and reversible. Requires a running Celery
worker + beat for it to actually fire.
"""

from django.db import migrations

TASK_PATH = 'apps.gas_guard.alerts.tasks.check_low_gas'
TASK_NAME = 'Low-Gas Check (hourly)'
INTERVAL_HOURS = 1


def create_schedule(apps, schema_editor):
    IntervalSchedule = apps.get_model('django_celery_beat', 'IntervalSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=INTERVAL_HOURS,
        period='hours',
    )
    PeriodicTask.objects.update_or_create(
        task=TASK_PATH,
        defaults={
            'name': TASK_NAME,
            'interval': schedule,
            'enabled': True,
        },
    )


def remove_schedule(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(task=TASK_PATH).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('gg_alerts', '0002_initial'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
