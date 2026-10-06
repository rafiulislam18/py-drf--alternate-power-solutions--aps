"""
Keep existing dashboard admins admin under the new role rule.

Before: any User without a ClientProfile was a dashboard admin. Now (public
sign-up exists) a missing profile only means admin for Django staff/superusers
— see apps.core.roles.get_role. So every existing profile-less, non-staff user
gets an explicit role='admin' profile, and nobody's access changes.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    ClientProfile = apps.get_model('core', 'ClientProfile')
    users = User.objects.filter(client_profile__isnull=True, is_staff=False, is_superuser=False)
    ClientProfile.objects.bulk_create([ClientProfile(user=u, role='admin') for u in users])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_profile_account_fields'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
