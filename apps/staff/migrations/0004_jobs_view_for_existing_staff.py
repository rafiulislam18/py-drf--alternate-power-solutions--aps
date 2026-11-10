"""
New read permission ``jobs.view`` (apps.jobs): new staff accounts get it by
default, so give it to the staff accounts that already exist too. Creating,
editing, assigning, commenting and deleting jobs stay off until a superuser
ticks them on Staff accounts.
"""

from django.db import migrations

CODE = 'jobs.view'


def grant(apps, schema_editor):
    StaffAccess = apps.get_model('staff', 'StaffAccess')
    for access in StaffAccess.objects.all():
        if CODE not in (access.permissions or []):
            access.permissions = [*(access.permissions or []), CODE]
            access.save(update_fields=['permissions'])


class Migration(migrations.Migration):

    dependencies = [
        ('staff', '0003_trash_view_for_existing_staff'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
