"""The first tool types (renamed, reordered, added or switched off on the
dashboard later). Tests skip migrations, so they call ``seed`` themselves."""

from django.db import migrations

TYPES = ['Drills', 'Grinders', 'Saws', 'Testers & meters', 'Crimpers', 'Ladders & access', 'Hand tools', 'Other']


def seed(apps, schema_editor):
    ToolType = apps.get_model('tools', 'ToolType')
    for order, name in enumerate(TYPES, start=1):
        ToolType.objects.get_or_create(name=name, defaults={'order': order})


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
