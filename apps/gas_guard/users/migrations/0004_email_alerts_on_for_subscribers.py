"""
The "Low-gas email alerts" switch is now respected (it used to be ignored and
everyone got the email). So nobody stops getting emails, switch it ON for
every owner who has a site with an active Monitoring subscription.

Data only (the default changed in 0003): kept separate from schema changes on
the same table, which Postgres refuses in one transaction.
"""

from django.db import migrations


def switch_on(apps, schema_editor):
    GasGuardUser = apps.get_model('gg_users', 'GasGuardUser')
    Subscription = apps.get_model('gg_subscription', 'Subscription')
    owner_ids = (Subscription.objects.filter(is_active=True, device__owner__isnull=False)
                 .values_list('device__owner_id', flat=True))
    GasGuardUser.objects.filter(pk__in=set(owner_ids)).update(email_alerts_enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ('gg_users', '0003_email_alerts_on_by_default'),
        ('gg_subscription', '0003_per_site_subscriptions'),
        ('gg_weight_scale', '0001_initial'),
    ]

    operations = [
        # Reversing leaves the switches as they are.
        migrations.RunPython(switch_on, migrations.RunPython.noop),
    ]
