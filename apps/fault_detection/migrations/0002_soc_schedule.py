"""
Register the 5-minute SOC imbalance check as a Celery Beat periodic task.

Runs automatically during `migrate` on deploy, so production gets the schedule
without a manual step. Idempotent and reversible.
"""

from django.db import migrations

TASK_PATH = 'apps.fault_detection.tasks.check_soc_imbalance'
TASK_NAME = 'SOC Imbalance Check (every 5 min)'
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
        ('fault_detection', '0001_initial'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
