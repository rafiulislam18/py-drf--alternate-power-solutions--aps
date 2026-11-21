"""
Register the hourly job that switches off cancelled plans once the month the
customer already paid for is over (apps.gas_guard.subscription.tasks.
end_due_subscriptions), at :10 past every hour. Idempotent and reversible;
applied automatically on `migrate`.
"""

from django.conf import settings
from django.db import migrations

TASK_PATH = 'apps.gas_guard.subscription.tasks.end_due_subscriptions'
TASK_NAME = 'Gas Guard: end cancelled subscriptions (hourly)'


def create_schedule(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    tz = getattr(settings, 'CELERY_TIMEZONE', 'Africa/Johannesburg')
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='10',
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
        ('gg_subscription', '0004_cancel_at_period_end'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_schedule, remove_schedule),
    ]
