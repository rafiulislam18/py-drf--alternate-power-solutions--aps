"""
Fold the invite-only client portal into dashboard accounts — step 1 of 3:
add the new ticket columns (client, new_site), nullable for now.

PostgreSQL can't ALTER a table in the same transaction that just inserted
rows referencing it ("pending trigger events"), so this is split into three
migrations, each in its own transaction: 0004 adds the new columns, 0005 moves
the data, 0006 drops the old columns and tables.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('client_portal', '0003_clientcontact_session_version'),
        ('core', '0003_keep_profileless_admins'),
        ('solar_dashboard', '0006_site_address_created_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='client',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='tickets', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='ticket',
            name='new_site',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='solar_dashboard.site'),
        ),
    ]
