from django.db import migrations, models


class Migration(migrations.Migration):
    """New-ticket email flags. Existing tickets start False: they were already emailed."""

    dependencies = [
        ('client_portal', '0010_ticket_service_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='team_alert_pending',
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name='ticket',
            name='client_alert_pending',
            field=models.BooleanField(default=False, editable=False),
        ),
    ]
