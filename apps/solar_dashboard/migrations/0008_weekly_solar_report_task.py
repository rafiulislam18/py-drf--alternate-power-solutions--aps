"""
Point the Monday 01:00 SAST schedule (registered in 0007) at the renamed task.

The weekly report moved from Home Assistant to EnergiTrack, so the task is now
apps.solar_dashboard.tasks.create_weekly_solar_report. Same crontab; only the
task path and name change. Reversible.
"""

from django.db import migrations

OLD_TASK = 'apps.solar_dashboard.tasks.create_weekly_ha_report'
OLD_NAME = 'Weekly HA Solar Report (Mondays 01:00 SAST)'
NEW_TASK = 'apps.solar_dashboard.tasks.create_weekly_solar_report'
NEW_NAME = 'Weekly Solar Report from EnergiTrack (Mondays 01:00 SAST)'


def rename_task(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(task=OLD_TASK).update(task=NEW_TASK, name=NEW_NAME)


def restore_task(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(task=NEW_TASK).update(task=OLD_TASK, name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ('solar_dashboard', '0007_weekly_ha_report_schedule'),
        ('django_celery_beat', '__latest__'),
    ]

    operations = [
        migrations.RunPython(rename_task, restore_task),
    ]
