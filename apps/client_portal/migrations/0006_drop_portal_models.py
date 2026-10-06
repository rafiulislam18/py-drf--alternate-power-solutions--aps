"""
Fold the invite-only client portal into dashboard accounts — step 3 of 3:
drop the old ticket columns, make client/site required, and delete the old
portal models (company, contacts, sign-in codes, portal sites).

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
        ('client_portal', '0005_move_portal_data_to_accounts'),
    ]

    operations = [
        migrations.RemoveIndex(model_name='ticket', name='client_port_company_d10480_idx'),
        migrations.RemoveIndex(model_name='ticket', name='client_port_company_5a4aa1_idx'),
        migrations.RemoveField(model_name='ticket', name='company'),
        migrations.RemoveField(model_name='ticket', name='created_by'),
        migrations.RemoveField(model_name='ticket', name='site'),
        migrations.RenameField(model_name='ticket', old_name='new_site', new_name='site'),
        migrations.AlterField(
            model_name='ticket',
            name='client',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name='tickets', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='ticket',
            name='site',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name='tickets', to='solar_dashboard.site'),
        ),
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(fields=['client', 'status'], name='ticket_client_status_idx'),
        ),
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(fields=['client', '-updated_at'], name='ticket_client_updated_idx'),
        ),

        migrations.DeleteModel(name='ClientSite'),
        migrations.DeleteModel(name='ClientContact'),
        migrations.DeleteModel(name='LoginCode'),
        migrations.DeleteModel(name='ClientCompany'),
    ]
