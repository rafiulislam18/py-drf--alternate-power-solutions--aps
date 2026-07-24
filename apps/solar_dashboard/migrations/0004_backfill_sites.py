"""
Backfill Site records from existing SiteData rows, then link each SiteData to its
matching Site.

For every existing SiteData whose report has a client, we get_or_create a Site keyed
on (client, site_name) and copy has_battery onto it (first-seen wins for battery /
order). SiteData rows on reports with NO client are left with site=NULL (can't own a
per-client Site); they keep their old site_name/has_battery columns and still work.

Reversible: the reverse simply nulls SiteData.site again (created Sites are left as
harmless extra rows — removing them could PROTECT-fail and isn't needed).
"""

from django.db import migrations


def backfill(apps, schema_editor):
    SiteData = apps.get_model('solar_dashboard', 'SiteData')
    Site = apps.get_model('solar_dashboard', 'Site')

    # Track next order per client so sites get a stable first-seen ordering.
    next_order = {}
    site_cache = {}  # (client_id, name) -> Site instance

    # Deterministic order: by report creation then row order, so "first seen" is stable.
    qs = (SiteData.objects
          .select_related('report')
          .order_by('report__created_at', 'report_id', 'order', 'id'))

    for sd in qs:
        client_id = sd.report.client_id
        if client_id is None:
            continue  # no client -> can't own a per-client Site; leave site NULL

        key = (client_id, sd.site_name)
        site = site_cache.get(key)
        if site is None:
            order = next_order.get(client_id, 0)
            site, created = Site.objects.get_or_create(
                client_id=client_id,
                name=sd.site_name,
                defaults={
                    'has_battery': sd.has_battery,
                    'is_active': True,
                    'order': order,
                },
            )
            if created:
                next_order[client_id] = order + 1
            elif sd.has_battery and not site.has_battery:
                # Any occurrence with a battery marks the site as battery-capable.
                site.has_battery = True
                site.save(update_fields=['has_battery'])
            site_cache[key] = site

        sd.site_id = site.id
        sd.save(update_fields=['site'])


def unlink(apps, schema_editor):
    SiteData = apps.get_model('solar_dashboard', 'SiteData')
    SiteData.objects.update(site=None)


class Migration(migrations.Migration):

    dependencies = [
        ('solar_dashboard', '0003_site_sitedata_site_site_uniq_site_per_client'),
    ]

    operations = [
        migrations.RunPython(backfill, unlink),
    ]
