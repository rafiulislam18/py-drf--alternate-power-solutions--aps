"""
Fill the company details printed on the Proposal & Quotation (VAT number,
office phone, 70% deposit) into an existing settings row where they are still
blank. Anything staff already typed is left alone. Data only (kept apart from
0002's schema changes for Postgres).
"""

from django.db import migrations

FILL = {'company_vat_number': '4480272055', 'company_phone': '068 319 3323'}


def fill(apps, schema_editor):
    EstimateSettings = apps.get_model('solar_estimates', 'EstimateSettings')
    for row in EstimateSettings.objects.all():
        changed = False
        for field, value in FILL.items():
            if not getattr(row, field):
                setattr(row, field, value)
                changed = True
        if row.deposit_percent is None:
            row.deposit_percent = 70
            changed = True
        if changed:
            row.save()


class Migration(migrations.Migration):

    dependencies = [
        ('solar_estimates', '0002_proposal_format'),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
