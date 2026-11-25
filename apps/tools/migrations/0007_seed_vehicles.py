"""The company vehicles (renamed, reordered, added or switched off on the
dashboard later). Tests skip migrations, so they call ``seed`` themselves."""

from django.db import migrations

VEHICLES = [
    ('Lorenzo NP200', 'CAA 636 441'),
    ('Gurshwain NP200', 'CAA 481 962'),
    ('Brian Isuzu', 'CAA 315 480'),
    ('Lucky Isuzu', 'CAA 650 272'),
    ('APS Suzuki EECO', 'CAA 760 335'),
    ('Leonard NP300', 'CAA 61880'),
]


def seed(apps, schema_editor):
    Vehicle = apps.get_model('tools', 'Vehicle')
    for order, (name, registration) in enumerate(VEHICLES, start=1):
        Vehicle.objects.get_or_create(name=name, defaults={'registration': registration, 'order': order})


class Migration(migrations.Migration):

    dependencies = [
        ('tools', '0006_vehicles'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
