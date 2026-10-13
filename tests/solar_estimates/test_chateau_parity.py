"""
Parity with the issued 22 Chateau Circle proposal (APS-PRJ-2610-CHC-001): the
filled-in workbook's Input tab (real meter readings) must reproduce every
non-price figure of the PDF. Prices come from the catalogue Ops maintains, so
the PDF's four prices are typed in as preview edits — then the escalated
payback, 10-year saving, net position and first-year return must match too.

The workbook holds supplier pricing and client details, so it is not in the
repo: set CHATEAU_ESTIMATE_PATH to "APS-EST 22 Chateau Circle Constantia.xlsx"
to run this (skipped otherwise).
"""

import os

import pytest

from apps.solar_estimates import document
from apps.solar_estimates.engine import calculate
from apps.solar_estimates.excel_import import import_workbook, read_inputs
from apps.solar_estimates.models import CatalogueItem, EstimateSettings
from apps.solar_estimates.numbering import suggest_site_code
from apps.solar_estimates.views import _settings_dict

PATH = os.getenv('CHATEAU_ESTIMATE_PATH', '')
pytestmark = [pytest.mark.django_db,
              pytest.mark.skipif(not os.path.exists(PATH), reason='set CHATEAU_ESTIMATE_PATH to the Chateau workbook')]

# The PDF's prices excl VAT (Ops' prices at the time — not reproduced from the catalogue).
PDF_PRICES = {'t1_split': '191150.15', 't2_split': '146525.95', 't1_full': '240009.65', 't2_full': '182990.75'}
# Month → (current bill, with solar, saving), page 4 of the PDF.
PDF_MONTHLY = {
    'Jan': ('R 7,923', 'R 2,574', 'R 5,349'), 'Feb': ('R 7,156', 'R 2,425', 'R 4,731'),
    'Mar': ('R 7,923', 'R 2,982', 'R 4,941'), 'Apr': ('R 7,667', 'R 3,495', 'R 4,172'),
    'May': ('R 7,923', 'R 4,552', 'R 3,371'), 'Jun': ('R 7,667', 'R 4,810', 'R 2,858'),
    'Jul': ('R 7,923', 'R 4,761', 'R 3,162'), 'Aug': ('R 7,923', 'R 4,029', 'R 3,893'),
    'Sep': ('R 7,667', 'R 3,065', 'R 4,602'), 'Oct': ('R 7,923', 'R 2,834', 'R 5,089'),
    'Nov': ('R 7,667', 'R 2,563', 'R 5,104'), 'Dec': ('R 7,923', 'R 2,537', 'R 5,386'),
}


def _build(edits=None):
    import_workbook(PATH)
    inputs = read_inputs(PATH)
    inputs['estimate_date'] = '2026-10-09'
    s = _settings_dict(EstimateSettings.load())
    r = calculate(inputs, s, [c.as_engine_dict() for c in CatalogueItem.objects.all()])
    return inputs, r, document.build(inputs, r, s, edits or {}, quote_number='APS-PRJ-2610-CHC-001')


def test_usage_sizing_and_monthly_table_match_the_pdf():
    inputs, r, doc = _build()
    s = r['sizing']
    assert round(s['daily'], 1) == 56.3 and r['usage']['status'].startswith('Good')
    assert doc['usage']['monthly'] == '1,712 kWh' and doc['usage']['annual_cost'] == 'R 93,284 incl. VAT'
    assert s['panels'] == 14 and round(s['annual_generation']) == 14237
    assert '14 × 620 W panels (8.7 kWp)' in doc['fields']['b_sized']
    assert doc['fields']['generation'].startswith('14,237 kWh a year – about 52%')
    assert {m['month']: (m['current'], m['with_solar'], m['saving']) for m in doc['monthly']} == PDF_MONTHLY
    savings = {o['key']: round(o['saving_client']) for o in r['options']}
    assert savings == {'t1_split': 52657, 't2_split': 52657, 't1_full': 56098, 't2_full': 56098}
    models = {o['key']: o['inverter']['model'] for o in r['options']}
    assert '10.0 TP' in models['t1_split'] and 'SUN-8K' in models['t2_split']
    assert '12.0 TP' in models['t1_full'] and 'SUN-12K' in models['t2_full']
    assert suggest_site_code(inputs['site_address']) == 'CHC'


def test_returns_match_the_pdf_with_its_prices():
    _, _, doc = _build({f'opt.{k}.subtotal': v for k, v in PDF_PRICES.items()})
    html = document.render_html(doc)
    for text in ['R 219,822.67', 'R 168,504.84', 'R 276,011.10', 'R 210,439.36',         # incl VAT
                 'R 817,408', 'R 870,817', 'R 597,585', 'R 648,903', 'R 594,806', 'R 660,378',
                 '3.7 years', '2.9 years', '4.2 years', '3.3 years',
                 'The recommended Deye smart split returns about 31% in the first year on its cost of '
                 'R 168,504.84 incl. VAT.']:
        assert text in html, text
    assert doc['pages'] == 5
