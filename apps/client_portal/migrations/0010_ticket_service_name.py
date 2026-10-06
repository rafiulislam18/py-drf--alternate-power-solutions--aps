"""
A ticket's service is the name of one of APS's real services (Site content →
Services), chosen live when the ticket is raised — no longer a fixed list.
Stored as text, with no link to the services table.

Existing ticket data was cleared before this change (the old fixed-list
values aren't carried over).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('client_portal', '0009_chat_digest_schedule'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticket',
            name='service',
            field=models.CharField(
                max_length=255,
                help_text='The APS service chosen when the ticket was raised (from Site content → Services).',
            ),
        ),
    ]
