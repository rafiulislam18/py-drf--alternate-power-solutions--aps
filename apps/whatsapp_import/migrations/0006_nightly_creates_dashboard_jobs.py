"""
The nightly 00:30 safety-net now turns marked messages into dashboard jobs
(tasks.create_jobs_from_whatsapp) instead of posting them to the Operations
sheet (tasks.export_jobs_to_sheet, removed). Same time, same schedule.
"""

from django.db import migrations

OLD = 'apps.whatsapp_import.tasks.export_jobs_to_sheet'
NEW = 'apps.whatsapp_import.tasks.create_jobs_from_whatsapp'
OLD_NAME = 'WhatsApp Jobs Export (nightly 00:30 SAST safety-net)'
NEW_NAME = 'WhatsApp → dashboard jobs (nightly 00:30 SAST safety-net)'


def forwards(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(task=OLD).update(task=NEW, name=NEW_NAME)


def backwards(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(task=NEW).update(task=OLD, name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp_import', '0005_message_job'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
