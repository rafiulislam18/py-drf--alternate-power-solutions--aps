import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('client_portal', '0012_new_ticket_alert_schedule'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatPresence',
            fields=[
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True,
                                              related_name='chat_presence', serialize=False, to=settings.AUTH_USER_MODEL)),
                ('side', models.CharField(choices=[('client', 'Client'), ('staff', 'APS staff')], db_index=True, max_length=6)),
                ('connections', models.PositiveIntegerField(default=0)),
                ('last_seen', models.DateTimeField(blank=True, null=True)),
            ],
        ),
    ]
