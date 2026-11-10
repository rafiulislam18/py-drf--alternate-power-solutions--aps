"""The categories the Operations sheet's Jobs tab used (22/10/2026), most used first."""

from django.db import migrations

CATEGORIES = [
    'Call out', 'Open jobs', 'Electrical', 'Inverter installation', 'Maintenance', "COC's", 'MLP',
    'Urban Growth', 'Assessment', 'Fault finding', 'SCADA', 'Other',
]


def seed(apps, schema_editor):
    JobCategory = apps.get_model('jobs', 'JobCategory')
    for order, name in enumerate(CATEGORIES, start=1):
        JobCategory.objects.get_or_create(name=name, defaults={'order': order})


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
