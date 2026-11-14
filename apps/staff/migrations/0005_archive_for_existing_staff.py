"""
New permissions ``<section>.archive`` for jobs, tickets, quote requests and
solar estimates (archive / unarchive). They aren't read permissions, so new
staff accounts start without them; staff who could already edit that section
get them, so archiving works for them from day one. Quote requests have no
"edit", so there it's whoever can view them.
"""

from django.db import migrations

# archive code → the code that already lets someone change that section
GRANTS = {
    'jobs.archive': 'jobs.edit',
    'tickets.archive': 'tickets.edit',
    'quotes.archive': 'quotes.view',
    'estimates.archive': 'estimates.edit',
}


def grant(apps, schema_editor):
    StaffAccess = apps.get_model('staff', 'StaffAccess')
    for access in StaffAccess.objects.all():
        have = list(access.permissions or [])
        new = [code for code, needs in GRANTS.items() if needs in have and code not in have]
        if new:
            access.permissions = [*have, *new]
            access.save(update_fields=['permissions'])


class Migration(migrations.Migration):

    dependencies = [
        ('staff', '0004_jobs_view_for_existing_staff'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
