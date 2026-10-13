"""
The client-facing document — "Solar & Battery Backup – Proposal & Quotation"
(the 22 Chateau Circle format, 10/2026). Turns the engine's numbers into the
proposal's wording, applies the staff's edits from the preview, and renders
HTML from ONE template + stylesheet for both the preview and the PDF.

Editable fields (pencil icons) are listed by ``editables``:
- 'text'   — the shown text is replaced as typed;
- 'number' — an option's price excl VAT or year-1 saving: VAT, total, payback,
             10-year figures and first-year return are worked out again;
- 'recalc' — daily use and panel count: sizing overrides, the whole proposal is
             recalculated (the frontend sends them as inputs);
- 'choice' — which option the first-year-return sentence names.

Documents confirmed in the earlier budget-estimate format (no ``version``) are
still rendered with the old template.
"""

import base64
import math
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

from django.conf import settings as dj_settings
from django.template.loader import render_to_string

from . import defaults
from .engine import returns

VERSION = 2
BRAND = {'Tier 1': 'Sigenergy', 'Tier 2': 'Deye'}
SCOPE = {'split': 'Smart split', 'full': 'Full cut-over'}
NO_INVERTER = 'No single unit fits – parallel / larger unit'


# ── formatting ───────────────────────────────────────────────────────────────

def _round(x, places=0):
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def money(x):
    """R 191,150.15 — prices keep their cents."""
    return f'R {_round(x, 2):,}'


def rands(x):
    """R 52,657 — savings in whole rands."""
    return f'R {_round(x):,}'


def number(x, places=0):
    return f'{_round(x, places):,}'


def years(x):
    return f'{_round(x, 1)} years' if x is not None else 'TBC'


def pct(x, places=0):
    return f'{_round(x * 100, places)}'.rstrip('0').rstrip('.') if places else f'{_round(x * 100)}'


def fmt_date(d):
    return d.strftime('%d/%m/%Y') if d else ''


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _to_number(value):
    try:
        return float(str(value).replace('R', '').replace(',', '').replace(' ', ''))
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def logo_data_uri():
    path = Path(dj_settings.BASE_DIR) / 'static' / 'logo.png'
    try:
        return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return ''


def image_data_uri(field_file):
    """The roof image embedded in the HTML, so the PDF printer needs no file access."""
    if not field_file:
        return ''
    try:
        with field_file.open('rb') as f:
            data = f.read()
    except (OSError, ValueError):
        return ''
    kind = 'png' if data[:8] == b'\x89PNG\r\n\x1a\n' else 'jpeg'
    return f'data:image/{kind};base64,' + base64.b64encode(data).decode()


# ── the document ─────────────────────────────────────────────────────────────

def _option_name(o, recommended=True):
    name = f"{BRAND[o['tier']]} {SCOPE[o['scope']].lower()}"
    return name


def _site_short(inputs):
    return (inputs.get('site_address') or '').strip()


def build(inputs, result, settings, edits=None, quote_number=''):
    """Display values for the proposal, with the staff's edits applied."""
    edits = dict(edits or {})
    s = result['sizing']
    usage = result['usage']
    prud = settings['prudence_factor']
    inc = settings.get('tariff_increase', 0.10)
    deg = settings.get('panel_degradation', 0.005)
    validity = int(inputs.get('validity_days') or settings.get('validity_days') or 7)
    quote_date = _parse_date(inputs.get('estimate_date'))
    split = s['lead_scope'] == 'split'
    site_short = _site_short(inputs)
    tier2_battery = next((o['battery']['model'] for o in result['options'] if o['tier'] == 'Tier 2' and o['battery']), 'lithium')

    supply = inputs.get('supply_phase') or 'Unknown'
    tbc = supply == 'Unknown' or bool(inputs.get('supply_to_be_confirmed'))
    breaker = inputs.get('main_breaker_a')
    supply_text = (f"{s['phase']}-phase" + (' (to be confirmed)' if tbc else '')
                   + (f' – {breaker} A main breaker' if breaker else '')
                   + f" – City of Cape Town, {inputs.get('tariff') or 'Home User'} tariff")
    backup = (inputs.get('existing_backup') or '').strip()
    details = (inputs.get('existing_backup_details') or '').strip()
    backup_text = 'None on site' if backup in ('', 'None') else backup + (f' – {details}' if details else '')
    site = site_short + (f" (Erf {inputs['erf_number']})" if site_short and inputs.get('erf_number') else '')

    roof_max = inputs.get('roof_max_panels')
    roof_w = inputs.get('roof_panel_watts') or settings['panel_watts']
    panels, kwp, watts = s['panels'], s['kwp'], settings['panel_watts']
    share = f"{_round(s['share_from_solar'] * prud * 100)}%"
    gen = number(s['annual_generation'])

    # ── options (prices, savings, escalated returns) — number edits first
    options = []
    for o in result['options']:
        k = o['key']
        subtotal, saving, priced = o['subtotal'], o['saving_client'], o['unpriced'] == 0
        if _to_number(edits.get(f'opt.{k}.subtotal')) is not None:
            subtotal, priced = _to_number(edits.pop(f'opt.{k}.subtotal')), True
        if _to_number(edits.get(f'opt.{k}.saving')) is not None:
            saving = _to_number(edits.pop(f'opt.{k}.saving'))
        vat = subtotal * settings['vat_rate']
        total = subtotal + vat
        back = returns(saving, total if priced else 0, inc, deg)
        options.append({
            'key': k, 'tier': o['tier'], 'scope': o['scope'], 'recommended': o['recommended'], 'priced': priced,
            'name': _option_name(o),
            'heading': f"{BRAND[o['tier']]} {SCOPE[o['scope']]}".upper() + (' (recommended)'.upper() if o['recommended'] else ''),
            'row_name': f"{BRAND[o['tier']]} – {SCOPE[o['scope']]}" + (' (recommended)' if o['recommended'] else ''),
            'roi_name': f"{BRAND[o['tier']]} – {SCOPE[o['scope']]}",
            'subtotal_value': subtotal, 'saving_value': saving, 'total_value': total if priced else None,
            'subtotal': money(subtotal) if priced else 'Price TBC',
            'vat': money(vat) if priced else 'Price TBC',
            'total': money(total) if priced else 'Price TBC',
            'saving': rands(saving),
            'payback_value': back['payback_years'] if priced else None,
            'payback': years(back['payback_years']) if priced else 'TBC',
            'ten_year': rands(back['ten_year_saving']),
            'net_10y': rands(back['net_10y']) if priced and back['net_10y'] is not None else 'TBC',
            'first_year_return': back['first_year_return'] if priced else None,
        })

    paybacks = [o['payback_value'] for o in options if o['payback_value'] is not None]
    pay_range = (f'{_round(min(paybacks), 1)} – {_round(max(paybacks), 1)} years' if paybacks else '')
    # The option named in the first-year-return sentence: the recommended one with
    # the shortest payback, unless the staff picked another on the preview.
    candidates = [o for o in options if o['recommended'] and o['payback_value'] is not None] or \
                 [o for o in options if o['payback_value'] is not None]
    default_roi = min(candidates, key=lambda o: o['payback_value'])['key'] if candidates else ''
    roi_key = edits.pop('roi_option', '')
    if roi_key not in {o['key'] for o in options}:
        roi_key = default_roi
    roi = next((o for o in options if o['key'] == roi_key), None)
    roi_sentence = ''
    if roi and roi['first_year_return'] is not None:
        roi_sentence = (f"The {'recommended ' if roi['recommended'] else ''}{roi['name']} returns about "
                        f"{_round(roi['first_year_return'] * 100)}% in the first year on its cost of "
                        f"{money(roi['total_value'])} incl. VAT.")

    fields = {
        'client': inputs.get('client_name') or '',
        'site': site,
        'supply': supply_text,
        'existing_backup': backup_text,
        'assessment': inputs.get('assessment') or defaults.DEFAULT_ASSESSMENT,
        'date': fmt_date(quote_date),
        'quote_no': quote_number or inputs.get('estimate_number') or '[on confirm]',
        'subtitle': f"{site_short} – {number(kwp, 1)} kWp solar with battery backup | Sigenergy and Deye options",
        'intro': (f"APS has reviewed the electricity use at {site_short} and proposes a {number(kwp, 1)} kWp solar "
                  "system with battery backup. Solar will carry the home’s daytime load and charge the batteries, "
                  "cutting the City electricity bill, while the batteries keep "
                  + ('the essential circuits' if split else 'the whole house') + ' running during outages. Four options '
                  'are priced across two equipment tiers so you can choose the balance of cost and features that '
                  'suits you.'),
        'b_sized': (f"{panels} × {watts:g} W panels ({number(kwp, 1)} kWp), producing about {gen} kWh a year – roughly "
                    f"{share} of the home’s electricity."
                    + (f' The roof can take up to {roof_max} panels if you want to add more later.' if roof_max else '')),
        'b_scope': ('your essential circuits are backed up; heavy loads such as geysers, ovens and underfloor heating '
                    'wait for the grid. This needs a smaller inverter and battery, so it costs less. A full cut-over of '
                    'the whole house is also priced.' if split else
                    'the whole house stays on during an outage. A smart split (essential circuits only) is also priced '
                    'as a lower-cost alternative.'),
        'b_tiers': ('Sigenergy (premium all-in-one system with a 10-year on-site swap-out warranty) or Deye with '
                    f'{tier2_battery} lithium batteries (proven and cost-effective).'),
        'b_compliant': ('installed to SANS 10142-1 and SANS 10142-1-2, registered with the City of Cape Town (SSEG), '
                        'and handed over with a CoC.'),
        'b_return': (f'estimated payback of about {pay_range}, based on a {pct(inc)}% annual tariff increase.'
                     if pay_range else 'payback is shown once the options are priced.'),
        'daily_use': f"{number(s['daily'], 1)} kWh",
        'tariff_name': f"City of Cape Town – {inputs.get('tariff') or 'Home User'}",
        'panels': (f"{panels} × Tier 1 {watts:g} W solar panels = {number(kwp, 1)} kWp, sized on your usage"
                   + (' (model to be confirmed)' if 'TBC' in next((o['panel']['model'] for o in result['options']), '')
                      else '')),
        'generation': f'{gen} kWh a year – about {share} (estimated) of your electricity',
        'approach': ('Smart split (recommended): existing DBs re-arranged into an essential (backed-up) board and a '
                     'non-essential board. Full cut-over priced as an alternative' if split else
                     'Full cut-over (recommended): the whole house backed up through a main changeover. Smart split '
                     'priced as an alternative'),
        'inverters': (f"Sigenergy or Deye {s['phase'].lower()}-phase hybrid inverter with lithium (LFP) batteries – "
                      'see the options in section 4'),
        'grid': ('Grid-tied; surplus exported under the City of Cape Town SSEG credit' if s['export_ok']
                 else 'Grid-tied, zero-export system (no export credit)'),
        'protection': ('DC isolators, DC and AC surge protection, earthing and bonding of array frames, UV-rated PV '
                       'cable and MC4 connectors'),
        'roof_caption': (f"Roof layout (HelioScope): the roof can take up to {roof_max} × {roof_w:g} W panels "
                         f"({number(roof_max * roof_w / 1000, 1)} kWp). This shows the roof’s maximum – the proposal "
                         f"itself is sized on your usage at {panels} panels." if roof_max else ''),
        'options_intro': ('The smart split keeps the backup system smaller and more affordable: solar still reduces the '
                          'bill for the whole house, while during an outage only the essential circuits stay on. A full '
                          'cut-over backs up the whole house and needs a larger inverter and battery bank.'),
        'tier_note': ('Sigenergy: premium all-in-one stackable system, weather-rated, with app monitoring. Deye: proven, '
                      f'cost-effective hybrid inverter with {tier2_battery} lithium batteries. Payback includes a '
                      f'{pct(inc)}% annual tariff increase.'),
        'roi_text': (f'Payback about {pay_range} depending on the option. {roi_sentence}' if pay_range else roi_sentence),
        'payment': (_payment(inputs, settings)),
    }
    for o in result['options']:
        k = o['key']
        bat = o['battery']
        fields[f'opt.{k}.inverter'] = o['inverter']['model'] if o['inverter'] else NO_INVERTER
        fields[f'opt.{k}.battery'] = f"{bat['qty']} × {bat['model']} ({number(bat['usable_total'], 1)} kWh)" if bat else '—'
        fields[f'opt.{k}.panels'] = f"{o['panel']['qty']} × {o['panel']['model']}"

    for key, value in edits.items():
        if key in fields and value is not None:
            fields[key] = str(value)

    lead_label = 'recommended smart split' if split else 'recommended full cut-over'
    monthly = [{
        'month': m['month'],
        'current': rands(m['bill_before']),
        'with_solar': rands(m['bill_before'] - m['saving'] * prud),
        'saving': rands(m['saving'] * prud),
    } for m in result['monthly']]
    year_before = sum(m['bill_before'] for m in result['monthly'])
    year_saving = sum(m['saving'] for m in result['monthly']) * prud
    basis = (f"Basis: {gen} kWh a year from APS Cape Town yield assumptions (about {number(s['yield_per_kwp'])} kWh per "
             f"kWp, after typical losses); {pct(s['daytime_share'])}% of daily use in daylight hours; current City "
             f"Home User tariffs incl. VAT (R {settings['block1_rate']:.2f} / R {settings['block2_rate']:.2f} per kWh) "
             f"and the R {settings['service_charge']:.2f} monthly service charge (unchanged by solar); "
             + (f"surplus exported at about R {settings['export_credit']:.2f}/kWh; " if s['export_ok']
                else 'zero-export system, so no export credit; ')
             + f"savings reduced by {pct(1 - prud)}% for prudence; {pct(inc)}% annual tariff increase and "
             f"{pct(deg, 1)}% annual panel output loss for payback and 10-year figures. These are estimates, not a guarantee.")

    company = settings
    header = ' | '.join(filter(None, [company.get('company_address'), company.get('company_phone'),
                                      company.get('company_email')])).upper()
    dep = settings.get('deposit_percent')
    return {
        'version': VERSION,
        'header': header,
        'fields': fields,
        'usage': {'monthly': f"{number(s['daily'] * 365 / 12)} kWh",
                  'annual_cost': f"{rands(s['current_annual_cost'])} incl. VAT",
                  'note': (f"Based on {usage['days']} days of actual meter readings, current CoCT tariffs incl VAT and "
                           'the fixed service charge.' if usage['days'] > 0 else
                           'Based on the daily use provided, current CoCT tariffs incl VAT and the fixed service charge.')},
        'options': options,
        'monthly': monthly,
        'monthly_title': f'Estimated monthly savings – {lead_label}',
        'year': {'current': rands(year_before), 'with_solar': rands(year_before - year_saving), 'saving': rands(year_saving)},
        'basis': basis,
        'warranty': defaults.SIGEN_WARRANTY,
        'warranty_conditions': defaults.SIGEN_CONDITIONS,
        'investment_note': defaults.INVESTMENT_NOTE,
        'scope': defaults.PROPOSAL_SCOPE,
        'timeline': defaults.TIMELINE,
        'closing': defaults.CLOSING,
        'terms': [
            ('Validity:', f'this quotation is valid for {validity} days from the date of issue, as equipment prices '
                          'are subject to supplier stock and exchange rates.'),
            ('Site assessment:', 'prices are based on a desktop assessment; any change found at the site assessment '
                                 'will be confirmed in writing before work proceeds.'),
            ('VAT:', f"APS is VAT-registered (VAT number {settings.get('company_vat_number') or '[confirm]'}). Prices "
                     f"are shown excluding VAT, with VAT at {pct(settings['vat_rate'])}% added."),
            ('Terms:', 'this quotation is subject to the APS Universal Terms and Conditions.'),
        ],
        'deposit': dep,
        'client_upper': (inputs.get('client_name') or 'THE CLIENT').upper(),
        'quote_date': fmt_date(quote_date),
        'valid_until': fmt_date(quote_date + timedelta(days=validity)) if quote_date else '',
        'roi_options': [{'key': o['key'], 'name': o['roi_name'] + (' (recommended)' if o['recommended'] else '')}
                        for o in options if o['first_year_return'] is not None],
        'roi_key': roi_key,
        'pages': 5,
    }


def _payment(inputs, settings):
    dep = inputs.get('deposit_percent')
    if dep in (None, ''):
        dep = settings.get('deposit_percent')
    if dep in (None, ''):
        return '[deposit % to confirm] on acceptance; balance on completion, before handover of the CoC.'
    dep = float(dep)
    return (f'{_round(dep)}% deposit on acceptance, to order the equipment and book the installation; '
            f'{_round(100 - dep)}% balance on completion, before handover of the CoC.')


# Field key → (label, kind[, input override for 'recalc']).
EDITABLE_LABELS = {
    'client': ('Client', 'text'),
    'site': ('Site', 'text'),
    'supply': ('Electricity supply', 'text'),
    'existing_backup': ('Existing backup', 'text'),
    'assessment': ('Assessment', 'text'),
    'date': ('Date', 'text'),
    'quote_no': ('Quotation number', 'text'),
    'subtitle': ('Subtitle', 'text'),
    'intro': ('Summary paragraph', 'text'),
    'b_sized': ('Summary: sized on your usage', 'text'),
    'b_scope': ('Summary: backup approach', 'text'),
    'b_tiers': ('Summary: equipment tiers', 'text'),
    'b_compliant': ('Summary: compliance', 'text'),
    'b_return': ('Summary: return', 'text'),
    'daily_use': ('Average daily use (kWh)', 'recalc', 'daily_kwh'),
    'tariff_name': ('Tariff', 'text'),
    'panels': ('Number of solar panels', 'recalc', 'panels'),
    'generation': ('Estimated generation', 'text'),
    'approach': ('Backup approach', 'text'),
    'inverters': ('Inverter and batteries', 'text'),
    'grid': ('Grid connection', 'text'),
    'protection': ('Protection', 'text'),
    'roof_caption': ('Roof layout caption', 'text'),
    'options_intro': ('Options introduction', 'text'),
    'tier_note': ('Options note', 'text'),
    'roi_text': ('Return on investment summary', 'text'),
    'payment': ('Payment terms', 'text'),
}
OPTION_LABELS = {
    'inverter': ('inverter', 'text'),
    'battery': ('batteries', 'text'),
    'panels': ('solar panels', 'text'),
    'subtotal': ('price excl VAT (R)', 'number'),
    'saving': ('year-1 saving (R)', 'number'),
}


def editables(doc, result):
    """What the preview may edit: {key: {label, kind, value, input?, choices?}}."""
    out = {}
    for key, meta in EDITABLE_LABELS.items():
        item = {'label': meta[0], 'kind': meta[1], 'value': doc['fields'].get(key, '')}
        if meta[1] == 'recalc':
            item['input'] = meta[2]
            item['value'] = round(result['sizing']['daily'], 2) if key == 'daily_use' else result['sizing']['panels']
        out[key] = item
    for opt in doc['options']:
        for part, (label, kind) in OPTION_LABELS.items():
            key = f"opt.{opt['key']}.{part}"
            value = (round(opt['subtotal_value'], 2) if part == 'subtotal' else
                     round(opt['saving_value'], 2) if part == 'saving' else doc['fields'].get(key, ''))
            out[key] = {'label': f"{opt['roi_name']}: {label}", 'kind': kind, 'value': value}
    out['roi_option'] = {'label': 'Option named in the first-year return', 'kind': 'choice', 'value': doc['roi_key'],
                         'choices': [{'value': o['key'], 'label': o['name']} for o in doc['roi_options']]}
    return out


def render_html(doc, editable=False, roof_image=''):
    """The proposal as a standalone HTML document (preview when ``editable``)."""
    template = 'solar_estimates/estimate.html' if doc.get('version') == VERSION else 'solar_estimates/estimate_v1.html'
    return render_to_string(template, {
        'doc': doc, 'f': doc['fields'], 'editable': editable, 'logo': logo_data_uri(), 'roof_image': roof_image,
    })
