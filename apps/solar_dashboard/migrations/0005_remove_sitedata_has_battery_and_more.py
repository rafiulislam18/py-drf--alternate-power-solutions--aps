# Cleanup migration: identity of a SiteData row now lives entirely on its linked
# Site, so the legacy site_name/has_battery columns are dropped and site becomes
# required (non-null). Migration 0004 backfilled + linked every existing row; the
# guard below aborts loudly if any unlinked row somehow remains, rather than letting
# the non-null alter fail obscurely or lose data.

import django.db.models.deletion
from django.db import migrations, models


def guard_all_linked(apps, schema_editor):
    SiteData = apps.get_model('solar_dashboard', 'SiteData')
    unlinked = SiteData.objects.filter(site__isnull=True).count()
    if unlinked:
        raise RuntimeError(
            f"Cannot drop legacy columns: {unlinked} SiteData row(s) have no linked "
            f"Site. Re-run the 0004 backfill or link them manually before migrating."
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('solar_dashboard', '0004_backfill_sites'),
    ]

    operations = [
        migrations.RunPython(guard_all_linked, noop),
        migrations.AlterField(
            model_name='sitedata',
            name='site',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='data_rows', to='solar_dashboard.site'),
        ),
        migrations.RemoveField(
            model_name='sitedata',
            name='has_battery',
        ),
        migrations.RemoveField(
            model_name='sitedata',
            name='site_name',
        ),
    ]
