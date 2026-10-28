"""
New read permission ``trash.view`` (apps.records): new staff accounts get it
by default, so give it to the staff accounts that already exist too — they
can then see and restore what they delete. Their other permissions are left
as they are.
"""

from django.db import migrations

CODE = 'trash.view'


def grant(apps, schema_editor):
    StaffAccess = apps.get_model('staff', 'StaffAccess')
    for access in StaffAccess.objects.all():
        if CODE not in (access.permissions or []):
            access.permissions = [*(access.permissions or []), CODE]
            access.save(update_fields=['permissions'])


class Migration(migrations.Migration):

    dependencies = [
        ('staff', '0002_keep_existing_staff_access'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
