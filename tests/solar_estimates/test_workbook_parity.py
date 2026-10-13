"""
Parity with the team's workbook: importing APS_Solar_Estimate_TEMPLATE.xlsx and
sizing the 9 Bel Ombre Drive site must reproduce the issued PDF estimate.

The workbook holds supplier pricing, so it is not in the repo: set
ESTIMATE_TEMPLATE_PATH to it to run these (skipped otherwise).
"""

import os

import pytest

from apps.solar_estimates.engine import calculate
from apps.solar_estimates.excel_import import import_workbook
from apps.solar_estimates.models import CatalogueItem, EstimateSettings
from apps.solar_estimates.views import _settings_dict

PATH = os.getenv('ESTIMATE_TEMPLATE_PATH', '')
pytestmark = [pytest.mark.django_db,
              pytest.mark.skipif(not os.path.exists(PATH), reason='set ESTIMATE_TEMPLATE_PATH to the APS workbook')]

# 9 Bel Ombre Drive: 75.51 kWh/day, three-phase, smart split with a 15 kW minimum inverter.
BEL_OMBRE = {'supply_phase': 'Three', 'backup_scope': 'split',
             'overrides': {'daily_kwh': 75.51, 'split_min_inverter_kw': 15}}


def test_bel_ombre_matches_the_issued_estimate():
    import_workbook(PATH)
    assert CatalogueItem.objects.filter(category='inverter').count() >= 10
    r = calculate(BEL_OMBRE, _settings_dict(EstimateSettings.load()), [c.as_engine_dict() for c in CatalogueItem.objects.all()])
    s = r['sizing']
    assert s['optimal_panels'] == 20 and round(s['annual_generation']) == 20338
    totals = {o['key']: (round(o['subtotal']), round(o['total']), round(o['saving_client']), round(o['payback_simple'], 1))
              for o in r['options']}
    assert totals == {
        't1_split': (187950, 216142, 73273, 2.9),
        't2_split': (180646, 207743, 73273, 2.8),
        't1_full': (255923, 294311, 80015, 3.7),
        't2_full': (257385, 295993, 80015, 3.7),
    }
    models = {o['key']: (o['inverter']['model'], o['battery']['qty']) for o in r['options']}
    assert models['t2_split'] == ('SUN-15K-SG05LP3-EU-SM2', 4)
    assert models['t1_full'] == ('SigenStor Energy Controller 20.0 TP', 4)
    jan = r['monthly'][0]
    assert round(jan['bill_before']) == 10721 and round(jan['saving'] * 0.9) == 7370 and round(jan['used']) == 1498
