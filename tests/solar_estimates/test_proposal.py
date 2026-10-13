"""
Proposal & Quotation format: quotation numbering (prefix-YYMM-SITE-NNN),
the editable site code, the option named in the first-year return, the roof
layout picture and the new settings.
"""

import io
from datetime import date

import pytest
from django.db import IntegrityError, transaction
from PIL import Image

from apps.solar_estimates.models import EstimateSettings, SolarEstimate
from apps.solar_estimates.numbering import clean_site_code, next_quote_number, suggest_site_code

from .conftest import inputs
from .test_api import BASE, preview


# ── numbering ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('address,code', [
    ('22 Chateau Circle, Constantia', 'CHC'),
    ('9 Bel Ombre Drive, Constantia', 'BED'),
    ('1 Main Road, Wynberg', 'MAR'),
    ('Kenilworth', 'KEN'),
    ('', ''),
])
def test_site_code_suggestion(address, code):
    assert suggest_site_code(address) == code


def test_site_code_is_cleaned():
    assert clean_site_code(' ch-c 1 ') == 'CHC1' and clean_site_code('abcdefgh') == 'ABCDEF'


def test_counter_resets_monthly_or_never():
    used = ['APS-PRJ-2609-CHC-001', 'APS-PRJ-2609-BED-002', 'APS-PRJ-2610-BED-001', 'OTHER-2610-BED-009']
    oct26 = date(2026, 10, 13)
    assert next_quote_number('APS-PRJ', oct26, 'chc', 'monthly', used) == 'APS-PRJ-2610-CHC-002'
    assert next_quote_number('APS-PRJ', date(2026, 11, 1), 'CHC', 'monthly', used) == 'APS-PRJ-2611-CHC-001'
    assert next_quote_number('APS-PRJ', date(2026, 11, 1), 'CHC', 'never', used) == 'APS-PRJ-2611-CHC-003'
    assert next_quote_number('APS', oct26, '', 'monthly', used) == 'APS-2610-XXX-001'


def test_numbers_are_unique_in_the_database(db):
    SolarEstimate.objects.create(number='APS-PRJ-2610-CHC-001')
    SolarEstimate.objects.create(number='')
    SolarEstimate.objects.create(number='')                     # blank drafts are fine
    with pytest.raises(IntegrityError), transaction.atomic():
        SolarEstimate.objects.create(number='APS-PRJ-2610-CHC-001')


def test_site_code_and_prefix_settings_shape_the_number(staff, catalogue):
    assert preview(staff, site_code='chc')['quote_number'] == 'APS-PRJ-2610-CHC-001'
    staff.put(f'{BASE}settings/', {'quote_prefix': 'APS-Q', 'quote_counter_reset': 'never'}, format='json')
    assert preview(staff)['quote_number'] == 'APS-Q-2610-MAR-001'
    assert staff.put(f'{BASE}settings/', {'quote_prefix': 'aps prj'}, format='json').status_code == 400


def test_typed_number_wins_and_duplicates_are_refused(staff, catalogue):
    a = staff.post(BASE, {'inputs': inputs()}, format='json').data['id']
    assert staff.post(f'{BASE}{a}/confirm/', {}, format='json').data['number'] == 'APS-PRJ-2610-MAR-001'
    b = staff.post(BASE, {'inputs': inputs(), 'edits': {'quote_no': 'APS-PRJ-2610-MAR-001'}}, format='json').data['id']
    res = staff.post(f'{BASE}{b}/confirm/', {}, format='json')
    assert res.status_code == 400 and 'already used' in res.data['detail']
    c = staff.post(BASE, {'inputs': inputs(estimate_number='APS-PRJ-2610-MAR-050')}, format='json').data['id']
    assert staff.post(f'{BASE}{c}/confirm/', {}, format='json').data['number'] == 'APS-PRJ-2610-MAR-050'
    # the counter carries on from the highest number used this month
    assert preview(staff)['quote_number'] == 'APS-PRJ-2610-MAR-051'


# ── preview choices and settings ─────────────────────────────────────────────

def test_roi_sentence_defaults_to_the_shortest_recommended_payback_and_can_be_changed(staff, catalogue):
    data = preview(staff)
    choice = data['editables']['roi_option']
    assert choice['value'] == 't2_split' and len(choice['choices']) == 4
    assert 'The recommended Deye smart split returns about' in data['html']
    assert 'The Sigenergy full cut-over returns about' in preview(staff, edits={'roi_option': 't1_full'})['html']
    assert 'The recommended Deye smart split returns about' in preview(staff, edits={'roi_option': 'nope'})['html']


def test_supply_to_be_confirmed_and_roof_caption(staff, catalogue):
    data = preview(staff, supply_to_be_confirmed=True, roof_max_panels=34, roof_panel_watts=595)
    assert data['editables']['supply']['value'].startswith('Three-phase (to be confirmed)')
    assert 'up to 34 × 595 W panels (20.2 kWp)' in data['editables']['roof_caption']['value']


def test_new_settings_validation(staff):
    for bad in ({'tariff_increase': 1.5}, {'panel_degradation': 0.5}, {'price_rounding': 'cents'},
                {'quote_counter_reset': 'yearly'}, {'deposit_percent': 120}):
        assert staff.put(f'{BASE}settings/', bad, format='json').status_code == 400, bad
    res = staff.put(f'{BASE}settings/', {'tariff_increase': 0.12, 'panel_degradation': 0.004,
                                        'price_rounding': 'nearest_100', 'deposit_percent': 60}, format='json')
    assert res.status_code == 200
    s = EstimateSettings.load()
    assert (s.tariff_increase, s.panel_degradation, s.price_rounding, s.deposit_percent) == (0.12, 0.004, 'nearest_100', 60)


def test_nearest_100_rounding_reaches_the_preview(staff, catalogue):
    staff.put(f'{BASE}settings/', {'price_rounding': 'nearest_100'}, format='json')
    eds = preview(staff)['editables']
    assert all(float(eds[f'opt.{k}.subtotal']['value']) % 100 == 0 for k in ('t1_split', 't2_split', 't1_full', 't2_full'))


# ── roof layout picture ──────────────────────────────────────────────────────

def _png(size=(2400, 1200)):
    buf = io.BytesIO()
    Image.new('RGB', size, (200, 80, 20)).save(buf, format='PNG')
    buf.seek(0)
    buf.name = 'roof.png'
    return buf


def test_roof_image_upload_preview_pdf_and_delete(staff, catalogue, settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = str(tmp_path)
    pk = staff.post(BASE, {'inputs': inputs(roof_max_panels=30)}, format='json').data['id']
    url = f'{BASE}{pk}/roof-image/'
    bad = io.BytesIO(b'not a picture')
    bad.name = 'roof.png'
    res = staff.post(url, {'file': bad}, format='multipart')
    assert res.status_code == 400 and 'not a picture' in res.data['file'][0]
    res = staff.post(url, {'file': _png()}, format='multipart')
    assert res.status_code == 200 and res.data['roof_image_url'].endswith('.jpg')
    est = SolarEstimate.objects.get(pk=pk)
    with Image.open(est.roof_image.path) as img:
        assert max(img.size) == 1800 and img.format == 'JPEG'     # shrunk and re-encoded
    data = staff.post(f'{BASE}preview/', {'inputs': inputs(roof_max_panels=30), 'estimate_id': pk},
                      format='json').data
    assert est.roof_image.name.split('/')[-1] in data['html']

    staff.post(f'{BASE}{pk}/confirm/', {}, format='json')
    assert staff.post(url, {'file': _png()}, format='multipart').status_code == 409   # issued → frozen
    seen = {}
    monkeypatch.setattr('apps.solar_estimates.views.html_to_pdf', lambda html: seen.update(html=html) or b'%PDF-1.7')
    assert staff.get(f'{BASE}{pk}/pdf/').status_code == 200
    assert 'src="data:image/jpeg;base64,' in seen['html']               # embedded for the printer

    staff.post(f'{BASE}{pk}/reopen/')
    assert not staff.delete(url).data['roof_image_url']
    assert not SolarEstimate.objects.get(pk=pk).roof_image


def test_roof_image_refuses_huge_pictures_before_decoding(staff, catalogue, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    pk = staff.post(BASE, {'inputs': inputs()}, format='json').data['id']
    buf = io.BytesIO()
    Image.new('1', (10_000, 5_000)).save(buf, format='PNG')    # 50 megapixels, but only a few KB on disk
    buf.seek(0)
    buf.name = 'roof.png'
    res = staff.post(f'{BASE}{pk}/roof-image/', {'file': buf}, format='multipart')
    assert res.status_code == 400 and 'too large' in res.data['file'][0]
    assert not SolarEstimate.objects.get(pk=pk).roof_image
