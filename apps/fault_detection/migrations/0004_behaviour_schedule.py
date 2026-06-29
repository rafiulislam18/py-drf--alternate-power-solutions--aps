"""
Register the 5-minute charge/discharge behaviour check as a Celery Beat task.

Runs automatically during `migrate` on deploy. Idempotent and reversible.
Reuses the existing 5-minute IntervalSchedule created by 0002.
"""

from django.db import migrations

TASK_PATH = 'apps.fault_detection.tasks.check_charge_behaviour'
TASK_NAME = 'Charge Behaviour Check (every 5 min)'
INTERVAL_MINUTES = 5


def create_schedule(apps, schema_editor):
    IntervalSchedule = apps.get_model('django_celery_beat', 'IntervalSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=INTERVAL_MINUTES,
        period='minutes',
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
        ('fault_detection', '0003_alertstate_consecutive_count'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
