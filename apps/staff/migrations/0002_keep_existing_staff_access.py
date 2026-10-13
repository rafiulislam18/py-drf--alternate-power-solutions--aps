"""
Before staff permissions existed, every staff account could do everything.
Keep it that way for the accounts that exist now: each non-superuser staff
account gets every permission (a superuser can then trim them on the Staff
accounts page). Accounts created later start with read permissions only.

Data only (kept apart from 0001's schema change for Postgres). The codes are
a snapshot so this migration never changes when the catalogue grows.
"""

from django.db import migrations
from django.db.models import Q

ALL_PERMS_AT_LAUNCH = [
    'reports.view', 'reports.create', 'reports.edit', 'reports.delete',
    'clients.view', 'clients.create', 'clients.edit', 'clients.delete',
    'tickets.view', 'tickets.create', 'tickets.edit', 'tickets.delete', 'tickets.assign',
    'tickets.chat_view', 'tickets.chat_send',
    'quotes.view',
    'estimates.view', 'estimates.create', 'estimates.edit', 'estimates.delete', 'estimates.confirm',
    'estimates.settings',
    'whatsapp.view', 'whatsapp.edit', 'whatsapp.export',
    'services.view', 'services.create', 'services.edit', 'services.delete',
    'projects.view', 'projects.create', 'projects.edit', 'projects.delete',
    'blogs.view', 'blogs.create', 'blogs.edit', 'blogs.delete',
]


def grant(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    StaffAccess = apps.get_model('staff', 'StaffAccess')
    staff = User.objects.filter(is_superuser=False).filter(
        Q(client_profile__role='admin') | Q(client_profile__isnull=True, is_staff=True)
    ).distinct()
    for user in staff:
        StaffAccess.objects.get_or_create(user=user, defaults={'permissions': ALL_PERMS_AT_LAUNCH})


class Migration(migrations.Migration):

    dependencies = [
        ('staff', '0001_initial'),
        ('core', '0003_keep_profileless_admins'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
