from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gg_alerts', '0003_hourly_schedule'),
    ]

    operations = [
        migrations.AddField(
            model_name='lowgasalertstate',
            name='is_critical',
            field=models.BooleanField(default=False),
        ),
    ]
