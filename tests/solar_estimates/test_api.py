"""
Solar estimates API: staff only, preview (HTML + pencils + edits), drafts,
confirm (numbering + frozen snapshot), reopen, PDF, settings and catalogue.
"""

import os

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.core.models import ClientProfile
from apps.solar_estimates.models import CatalogueItem, EstimateSettings, SolarEstimate

from .conftest import inputs

pytestmark = pytest.mark.django_db
BASE = '/solar-estimates/'


def preview(api, edits=None, **over):
    res = api.post(f'{BASE}preview/', {'inputs': inputs(**over), 'edits': edits or {}}, format='json')
    assert res.status_code == 200, res.data
    return res.data


def test_staff_only(catalogue):
    assert APIClient().post(f'{BASE}preview/', {}, format='json').status_code == 401
    user = User.objects.create_user('acme')
    ClientProfile.objects.create(user=user, role='client')
    api = APIClient()
    api.force_authenticate(user)
    for method, url in [('post', 'preview/'), ('get', ''), ('get', 'settings/'), ('post', 'catalogue/')]:
        assert getattr(api, method)(f'{BASE}{url}', {}, format='json').status_code == 403


def test_preview_renders_the_estimate_with_pencils(staff, catalogue):
    data = preview(staff)
    html = data['html']
    assert 'SOLAR &amp; BATTERY BACKUP – PROPOSAL &amp; QUOTATION' in html and 'Page 5 | 5' in html
    assert 'class="pen" data-edit="client"' in html and 'data-edit="opt.t2_split.subtotal"' in html
    assert '1 Main Road, Wynberg (Erf 123)' in html
    assert '75.5 kWh' in html and 'Deye – Smart split (recommended)' in html
    assert data['editables']['panels']['kind'] == 'recalc' and data['editables']['panels']['input'] == 'panels'
    assert data['editables']['opt.t1_full.subtotal']['kind'] == 'number'
    assert data['editables']['roi_option']['kind'] == 'choice'
    assert data['summary']['sizing']['panels'] > 0 and data['warnings'] == []
    # provisional quotation number: prefix-YYMM-SITE-NNN, site code suggested from the street
    assert data['quote_number'] == 'APS-PRJ-2610-MAR-001' and data['site_code'] == 'MAR'
    assert 'Solar used' not in html                         # removed from the monthly table


def test_text_and_price_edits(staff, catalogue):
    base = preview(staff)['editables']
    saving = base['opt.t2_split.saving']['value']
    data = preview(staff, edits={'client': 'Edited Trust <b>', 'opt.t2_split.subtotal': '100000'})
    html = data['html']
    assert 'Edited Trust &lt;b&gt;' in html                # escaped, never raw HTML
    assert 'R 100,000.00' in html and 'R 115,000.00' in html   # excl → incl VAT recomputed, with cents
    from apps.solar_estimates.engine import returns
    back = returns(saving, 115000, 0.10, 0.005)['payback_years']
    assert f'{back:.1f} years' in html                     # escalated payback from the edited price


def test_missing_readings_warn(staff, catalogue):
    data = preview(staff, readings=[])
    assert data['warnings'][0]['level'] == 'error'
    assert preview(staff, readings=[], overrides={'daily_kwh': 40})['warnings'] == []


def test_unpriced_lines_show_price_tbc(staff, catalogue):
    CatalogueItem.objects.filter(code='coc').update(cost_excl_vat=None)
    data = preview(staff)
    assert 'Price TBC' in data['html'] and any('Price TBC' in w['text'] for w in data['warnings'])


def test_draft_confirm_number_snapshot_and_reopen(staff, catalogue):
    res = staff.post(BASE, {'inputs': inputs(), 'edits': {'client': 'Lerato M'}}, format='json')
    assert res.status_code == 201
    pk = res.data['id']
    est = SolarEstimate.objects.get(pk=pk)
    assert est.status == 'draft' and est.client_name == 'Test Trust' and est.total_incl_vat > 0

    res = staff.post(f'{BASE}{pk}/confirm/', {}, format='json')
    assert res.status_code == 200, res.data
    est.refresh_from_db()
    assert est.status == 'issued' and est.number == 'APS-PRJ-2610-MAR-001'
    assert est.snapshot['fields']['client'] == 'Lerato M' and est.snapshot['version'] == 2
    assert 'class="pen"' not in res.data['html']           # confirmed view has no pencils

    # Prices change later: the confirmed estimate keeps its numbers.
    before = est.snapshot['options'][1]['total']
    CatalogueItem.objects.update(cost_excl_vat=1)
    assert staff.get(f'{BASE}{pk}/').data['html'].count(before) >= 1
    assert staff.patch(f'{BASE}{pk}/', {'inputs': inputs()}, format='json').status_code == 409

    res = staff.post(f'{BASE}{pk}/reopen/')
    assert res.data['status'] == 'draft'
    second = staff.post(BASE, {'inputs': inputs()}, format='json').data['id']
    assert staff.post(f'{BASE}{second}/confirm/', {}, format='json').data['number'] == 'APS-PRJ-2610-MAR-002'


def test_confirm_needs_usable_consumption(staff, catalogue):
    pk = staff.post(BASE, {'inputs': inputs(readings=[])}, format='json').data['id']
    res = staff.post(f'{BASE}{pk}/confirm/', {}, format='json')
    assert res.status_code == 400 and 'readings' in res.data['detail']


def test_list_search_and_delete(staff, catalogue):
    staff.post(BASE, {'inputs': inputs(client_name='Bel Ombre Trust')}, format='json')
    pk = staff.post(BASE, {'inputs': inputs(client_name='Seaview Estates')}, format='json').data['id']
    res = staff.get(BASE, {'q': 'seaview'})
    assert [r['client_name'] for r in res.data['results']] == ['Seaview Estates']
    assert staff.delete(f'{BASE}{pk}/').status_code == 204
    assert staff.get(BASE).data['count'] == 1


def test_pdf_only_after_confirm(staff, catalogue, monkeypatch):
    pk = staff.post(BASE, {'inputs': inputs()}, format='json').data['id']
    assert staff.get(f'{BASE}{pk}/pdf/').status_code == 409
    staff.post(f'{BASE}{pk}/confirm/', {}, format='json')
    seen = {}

    def fake_pdf(html):
        seen['html'] = html
        return b'%PDF-1.7 fake'
    monkeypatch.setattr('apps.solar_estimates.views.html_to_pdf', fake_pdf)
    res = staff.get(f'{BASE}{pk}/pdf/')
    assert res.status_code == 200 and res['Content-Type'] == 'application/pdf'
    assert res.content.startswith(b'%PDF')
    assert 'APS-PRJ-2610-MAR-001 1 Main Road Solar & Battery Backup.pdf' in res['Content-Disposition']
    assert 'class="pen"' not in seen['html'] and 'Test Trust' in seen['html']


@pytest.mark.skipif(os.getenv('ESTIMATE_PDF_E2E') != '1', reason='set ESTIMATE_PDF_E2E=1 to print with a real browser')
def test_real_pdf(staff, catalogue):
    pk = staff.post(BASE, {'inputs': inputs()}, format='json').data['id']
    staff.post(f'{BASE}{pk}/confirm/', {}, format='json')
    res = staff.get(f'{BASE}{pk}/pdf/')
    assert res.status_code == 200 and res.content[:5] == b'%PDF-' and len(res.content) > 20_000


def test_settings_and_catalogue(staff, catalogue):
    data = staff.get(f'{BASE}settings/').data
    assert data['settings']['block2_rate'] == 4.6906 and len(data['catalogue']) > 10
    panel = next(c for c in data['catalogue'] if c['category'] == 'panel')
    assert panel['sell_excl_vat'] == 1667
    assert staff.put(f'{BASE}settings/', {'vat_rate': 1.5}, format='json').status_code == 400
    assert staff.put(f'{BASE}settings/', {'company_vat_number': '4123456789', 'target_margin': 0.2},
                     format='json').status_code == 200
    assert EstimateSettings.load().company_vat_number == '4123456789'
    assert 'VAT number 4123456789' in preview(staff)['html']

    res = staff.patch(f"{BASE}catalogue/{panel['id']}/", {'cost_excl_vat': '1600.00'}, format='json')
    assert res.status_code == 200 and res.data['sell_excl_vat'] == 2000   # 1600 ÷ (1 − 0.2)
    res = staff.post(f'{BASE}catalogue/', {'category': 'inverter', 'tier': 'Tier 2', 'model': 'New 12K',
                                          'phase': 'Three', 'rated_kw': 12, 'max_pv_kwp': 15.6}, format='json')
    assert res.status_code == 201
    assert staff.delete(f"{BASE}catalogue/{res.data['id']}/").status_code == 204


def test_half_filled_catalogue_rows_warn_instead_of_failing(staff, catalogue):
    # A battery added with "Add" and not filled in yet: goes to the end, so the sized battery stays in use.
    res = staff.post(f'{BASE}catalogue/', {'category': 'battery', 'tier': 'Tier 2', 'model': 'New item'}, format='json')
    assert res.status_code == 201
    assert preview(staff)['summary']['options'][1]['battery']['model'] == 'T2 Battery 5.12'
    # Moved to the top / the only Tier 2 battery: still a preview, with a warning.
    CatalogueItem.objects.filter(category='battery', tier='Tier 2').exclude(model='New item').delete()
    data = preview(staff)
    t2 = data['summary']['options'][1]
    assert t2['battery']['qty'] == 0 and t2['payback_simple'] is not None
    assert any('“New item” has no usable kWh' in w['text'] for w in data['warnings'])
    CatalogueItem.objects.filter(category='battery').delete()
    assert any('no Tier 1 battery in the catalogue' in w['text'] for w in preview(staff)['warnings'])


def test_there_is_no_workbook_upload(staff):
    assert staff.post(f'{BASE}import/', {}, format='multipart').status_code in (404, 405)


def test_the_command_rejects_non_workbooks(tmp_path):
    from django.core.management import CommandError, call_command
    bad = tmp_path / 'x.xlsx'
    bad.write_bytes(b'not excel')
    with pytest.raises(CommandError, match='Excel'):
        call_command('import_estimate_template', str(bad))


@pytest.mark.skipif(not os.path.exists(os.getenv('ESTIMATE_TEMPLATE_PATH', '')), reason='needs the APS workbook')
def test_the_command_keeps_details_the_workbook_leaves_blank(db):
    from django.core.management import call_command
    s = EstimateSettings.load()
    s.company_vat_number, s.company_phone = '4480272055', '068 319 3323'
    s.save()
    call_command('import_estimate_template', os.environ['ESTIMATE_TEMPLATE_PATH'])
    s = EstimateSettings.load()
    assert CatalogueItem.objects.count() >= 25 and len(s.yield_profile) == 12
    assert (s.company_vat_number, s.company_phone) == ('4480272055', '068 319 3323')   # blank cells don't wipe them


def test_field_errors_name_the_field_in_form_units(staff, catalogue):
    res = staff.post(f'{BASE}preview/', {'inputs': inputs(essential_share=0.03, max_panels=2)}, format='json')
    assert res.status_code == 400
    assert res.data['essential_share'] == ['Enter a percentage between 5 and 100.']   # not "0.05"
    assert 'max_panels' in res.data


def test_confirm_needs_client_and_site_address(staff, catalogue):
    pk = staff.post(BASE, {'inputs': inputs(client_name='', site_address=' ')}, format='json').data['id']
    res = staff.post(f'{BASE}{pk}/confirm/', {}, format='json')
    assert res.status_code == 400
    assert res.data['client_name'] == ['Enter the client / entity name.'] and 'site_address' in res.data
    assert SolarEstimate.objects.get(pk=pk).status == 'draft'
    # no consumption: the readings are named
    pk = staff.post(BASE, {'inputs': inputs(readings=[])}, format='json').data['id']
    assert 'readings' in staff.post(f'{BASE}{pk}/confirm/', {}, format='json').data
