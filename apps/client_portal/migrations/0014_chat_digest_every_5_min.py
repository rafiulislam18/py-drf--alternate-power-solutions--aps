"""
Run the unread-chat email job every 5 minutes (was every 30). A message is now
emailed once it has gone unread for 30 minutes (``digests.MIN_AGE``), so this
sends it 30–35 minutes after it was written. Reversible.
"""

from django.conf import settings
from django.db import migrations

TASK_PATH = 'apps.client_portal.tasks.send_ticket_chat_digests'


def _set_minute(apps, minute, name):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    tz = getattr(settings, 'CELERY_TIMEZONE', 'Africa/Johannesburg')
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=minute, hour='*', day_of_week='*', day_of_month='*', month_of_year='*', timezone=tz,
    )
    PeriodicTask.objects.update_or_create(
        task=TASK_PATH,
        defaults={'name': name, 'crontab': schedule, 'interval': None, 'enabled': True},
    )


def every_5(apps, schema_editor):
    _set_minute(apps, '*/5', 'Ticket chat: messages unread for 30 min (checked every 5 min)')


def every_30(apps, schema_editor):
    _set_minute(apps, '0,30', 'Ticket chat: unread-message emails (every 30 min)')


class Migration(migrations.Migration):

    dependencies = [
        ('client_portal', '0013_chatpresence'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(every_5, every_30),
    ]
