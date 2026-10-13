"""
Solar & battery sizing engine — a plain-Python copy of the APS estimate
workbook (APS_Solar_Estimate_TEMPLATE.xlsx: Sizing + Options tabs).

Deterministic on purpose: no AI, no randomness, so the same inputs always give
the same numbers. Cell references in the comments point at the workbook.

    calculate(inputs, settings, catalogue) -> dict

``inputs``    validated estimate inputs (see serializers.EstimateInputSerializer)
``settings``  assumptions dict (see defaults.ASSUMPTIONS)
``catalogue`` list of catalogue dicts (see models.CatalogueItem.as_engine_dict)
"""

import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

SWEEP_START = 4
SWEEP_ROWS = 30          # Sizing!A36:A65
BATTERY_MARGIN = 0.95    # Options!B14: modules = ROUNDUP(target × 0.95 ÷ module kWh)
MONTH_LEN = 365 / 12     # tariff proration base


def _ceil(x):
    """Excel ROUNDUP(x, 0) without float noise turning 15.000000001 into 16."""
    return math.ceil(round(x, 9))


def sell_price(cost, margin, rounding='item'):
    """Catalogue!M: cost ÷ (1 − margin), rounded to whole rands like the workbook
    (``rounding='item'``) or kept exact for the other modes; unpriced (blank) → 0."""
    if cost is None or cost == '':
        return 0
    value = Decimal(str(cost)) / (Decimal(1) - Decimal(str(margin)))
    if rounding == 'item':
        return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return float(value)


def round_total(subtotal, rounding):
    """'nearest_10' / 'nearest_100': the total excl VAT to the nearest R10 / R100 (half up)."""
    step = {'nearest_10': 10, 'nearest_100': 100}.get(rounding)
    if not step or not subtotal:
        return subtotal
    return float((Decimal(str(subtotal)) / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step)


def returns(year1_saving, total_incl_vat, tariff_increase, panel_degradation, years=10):
    """Escalated return figures (Proposal & Quotation format):

    year-n saving = year-1 × ((1 + tariff increase) × (1 − panel degradation))^(n−1);
    payback = years until the running total reaches the price incl VAT, with the
    last year counted in part; plus the 10-year saving, 10-year net position and
    first-year return.
    """
    growth = (1 + tariff_increase) * (1 - panel_degradation)
    out = {'growth': growth, 'payback_years': None, 'ten_year_saving': 0.0, 'net_10y': None, 'first_year_return': None}
    if year1_saving <= 0:
        return out
    out['ten_year_saving'] = sum(year1_saving * growth ** n for n in range(years))
    if total_incl_vat:
        out['net_10y'] = out['ten_year_saving'] - total_incl_vat
        out['first_year_return'] = year1_saving / total_incl_vat
        cumulative = 0.0
        for n in range(1, 101):
            saving = year1_saving * growth ** (n - 1)
            if cumulative + saving >= total_incl_vat:
                out['payback_years'] = n - 1 + (total_incl_vat - cumulative) / saving
                break
            cumulative += saving
    return out


# ── consumption from municipal readings (Input!F5:F14) ───────────────────────

def consumption_from_readings(readings):
    """Daily average from the ACTUAL meter readings; estimates are ignored."""
    actual = [r for r in readings if (r.get('type') or 'Actual') == 'Actual' and r.get('date') and r.get('reading') is not None]
    if not actual:
        return {'first_date': None, 'last_date': None, 'days': 0, 'kwh_used': 0.0, 'daily': 0.0}
    dates = [r['date'] if isinstance(r['date'], date) else date.fromisoformat(str(r['date'])) for r in actual]
    values = [float(r['reading']) for r in actual]
    days = (max(dates) - min(dates)).days
    used = max(values) - min(values)
    return {
        'first_date': min(dates), 'last_date': max(dates), 'days': days, 'kwh_used': used,
        'daily': used / days if days > 0 else 0.0,
    }


def data_status(days):
    if days < 20:
        return 'error', 'Need at least 2 actual readings ≥20 days apart'
    if days < 60:
        return 'ok', 'OK – more history improves accuracy'
    return 'good', 'Good'


# ── tariff + per-day model ───────────────────────────────────────────────────

def _energy_cost(kwh, days, s):
    """Block 1 up to the prorated threshold, block 2 above it (energy only)."""
    threshold = s['block1_threshold'] * days / MONTH_LEN
    return min(kwh, threshold) * s['block1_rate'] + max(kwh - threshold, 0) * s['block2_rate']


def _per_day(kwp, yield_, daily, day_share, battery_cap, export_ok, s):
    """Sizing!K/X: solar used on site and export, kWh per day."""
    gen = kwp * yield_
    day_use = daily * day_share
    direct = min(gen, day_use)
    battery = min(max(gen - day_use, 0) * s['battery_efficiency'], battery_cap, daily * (1 - day_share))
    used = direct + battery
    export = max(gen - direct - battery / s['battery_efficiency'], 0) if export_ok else 0.0
    return used, export


def _monthly(kwp, daily, day_share, battery_cap, export_ok, s):
    """Sizing!A70:J81 — one row per month for a given battery capacity."""
    rows = []
    for m in s['yield_profile']:
        days = m['days']
        used, export = _per_day(kwp, m['yield'], daily, day_share, battery_cap, export_ok, s)
        consumption = daily * days
        service = s['service_charge'] * days / MONTH_LEN
        before = _energy_cost(consumption, days, s) + service
        grid = consumption - used * days
        after = _energy_cost(grid, days, s) + service - export * days * s['export_credit']
        rows.append({
            'month': m['month'], 'days': days, 'consumption': consumption,
            'generation': kwp * m['yield'] * days, 'used': used * days, 'export': export * days,
            'grid': grid, 'bill_before': before, 'bill_after': after, 'saving': before - after,
        })
    return rows


def _sweep(daily, day_share, battery_cap, export_ok, s):
    """Sizing!A36:I65 — annual saving for 4, 6, 8 … panels and the marginal value per added panel."""
    rows, prev = [], None
    panels = SWEEP_START
    for _ in range(SWEEP_ROWS):
        kwp = panels * s['panel_watts'] / 1000
        saving = 0.0
        for m in s['yield_profile']:
            days = m['days']
            used, export = _per_day(kwp, m['yield'], daily, day_share, battery_cap, export_ok, s)
            saving += (_energy_cost(daily * days, days, s) - _energy_cost((daily - used) * days, days, s)
                       + export * days * s['export_credit'])
        marginal = saving / panels if prev is None else (saving - prev[1]) / (panels - prev[0])
        rows.append({'panels': panels, 'kwp': kwp, 'saving': saving, 'marginal': marginal})
        prev = (panels, saving)
        panels += s['sweep_step']
    return rows


# ── equipment options (Options tab) ──────────────────────────────────────────

def _pick_inverter(catalogue, tier, phase, min_kw, kwp):
    """Smallest unit for tier & phase with kW ≥ minimum and PV input ≥ array (Options!B9)."""
    fits = [c for c in catalogue if c['category'] == 'inverter' and c['tier'] == tier and c['phase'] == phase
            and c.get('rated_kw') is not None and c.get('max_pv_kwp') is not None
            and c['rated_kw'] >= min_kw and c['max_pv_kwp'] >= kwp]
    return min(fits, key=lambda c: c['rated_kw']) if fits else None


def _find(catalogue, **match):
    return next((c for c in catalogue if all(c.get(k) == v for k, v in match.items())), None)


def _option(tier, scope, ctx, catalogue, s):
    margin = s['target_margin']
    rounding = s.get('price_rounding') or 'item'

    def sell(cost):
        return sell_price(cost, margin, rounding)
    split = scope == 'split'
    min_kw = ctx['split_min_kw'] if split else ctx['full_min_kw']
    inverter = _pick_inverter(catalogue, tier, ctx['phase'], min_kw, ctx['kwp'])
    battery = _find(catalogue, category='battery', tier=tier)
    panel = _find(catalogue, category='panel')
    target = ctx['battery_split'] if split else ctx['battery_full']

    qty = 0
    if battery and battery.get('usable_kwh'):
        qty = max(1, min(_ceil(target * BATTERY_MARGIN / battery['usable_kwh']), battery.get('max_modules') or 10**6))

    def bos(code):
        item = _find(catalogue, category='bos', code=code)
        return sell(item['cost']) if item else 0

    panels, kwp = ctx['panels'], ctx['kwp']
    lines = [
        ('Inverter', sell(inverter['cost']) if inverter else 0),
        ('Battery modules', qty * sell(battery['cost']) if battery else 0),
        ('Solar panels', panels * sell(panel['cost']) if panel else 0),
        ('Mounting & rails', panels * bos('mounting')),
        ('DC protection kit', bos('dc_kit')),
        ('AC protection, DB & changeover', bos('ac_split' if split else 'ac_full')),
        ('Sigen Gateway / battery cabinet', bos('gateway_t1' if tier == 'Tier 1' else 'cabinet_t2')),
        ('Cabling & sundries', kwp * bos('cabling')),
        ('Installation labour', kwp * bos('labour')),
        ('Electrical COC', bos('coc')),
        ('CoCT SSEG application & sign-off', bos('sseg')),
        ('Transport & P&G', bos('transport')),
    ]
    subtotal = round_total(sum(amount for _, amount in lines), rounding)
    vat = subtotal * s['vat_rate']
    saving_model = ctx['saving_split'] if split else ctx['saving_full']
    saving_client = saving_model * s['prudence_factor']
    total = subtotal + vat
    back = returns(saving_client, total, s.get('tariff_increase', 0.10), s.get('panel_degradation', 0.005))
    return {
        'key': f"{'t1' if tier == 'Tier 1' else 't2'}_{scope}",
        'tier': tier,
        'scope': scope,
        'recommended': scope == ctx['lead_scope'],
        'phase': ctx['phase'],
        'min_inverter_kw': min_kw,
        'inverter': ({'model': inverter['model'], 'rated_kw': inverter['rated_kw'], 'max_pv_kwp': inverter['max_pv_kwp']}
                     if inverter else None),
        # A catalogue row without usable kWh (e.g. just added) can't be sized: qty 0, flagged by the view.
        'battery': ({'model': battery['model'], 'qty': qty, 'usable_each': battery.get('usable_kwh'),
                     'usable_total': qty * (battery.get('usable_kwh') or 0)} if battery else None),
        'panel': {'model': panel['model'] if panel else '', 'qty': panels},
        'lines': [{'label': label, 'amount': amount} for label, amount in lines],
        'unpriced': sum(1 for _, amount in lines if not amount),
        'subtotal': subtotal,
        'vat': vat,
        'total': total,
        'saving_model': saving_model,
        'saving_client': saving_client,
        'payback_simple': total / saving_client if saving_client > 0 else None,
        'payback_years': back['payback_years'],
        'ten_year_saving': back['ten_year_saving'],
        'net_10y': back['net_10y'],
        'first_year_return': back['first_year_return'],
        'cost_excl_vat': subtotal * (1 - margin),
    }


# ── main entry point ─────────────────────────────────────────────────────────

def calculate(inputs, settings, catalogue):
    s = settings
    ov = inputs.get('overrides') or {}
    usage = consumption_from_readings(inputs.get('readings') or [])
    level, message = data_status(usage['days'])

    daily = float(ov['daily_kwh']) if ov.get('daily_kwh') not in (None, '') else usage['daily']          # Sizing!B7
    day_share = (float(inputs['daytime_share_override']) if inputs.get('daytime_share_override') not in (None, '')
                 else s['daytime_share'])                                                                # B8
    max_panels = int(inputs.get('max_panels') or 60)                                                      # B9
    essential = float(inputs.get('essential_share') if inputs.get('essential_share') not in (None, '') else 0.5)  # B11
    lead_scope = 'full' if inputs.get('backup_scope') == 'full' else 'split'                              # B19
    export_ok = inputs.get('export_to_grid', True) is not False                                          # B16

    battery_full_calc = daily * (1 - day_share) * s['battery_night_target']                              # B10
    battery_full = float(ov['battery_kwh']) if ov.get('battery_kwh') not in (None, '') else battery_full_calc  # B14
    battery_split = battery_full * essential                                                             # B17
    battery_lead = battery_split if lead_scope == 'split' else battery_full                              # B12
    battery_other = battery_full if lead_scope == 'split' else battery_split

    full_min_kw = (float(ov['full_min_inverter_kw']) if ov.get('full_min_inverter_kw') not in (None, '')
                   else max(5, _ceil(daily / 5)))                                                        # B13/B15
    split_min_kw = (float(ov['split_min_inverter_kw']) if ov.get('split_min_inverter_kw') not in (None, '')
                    else max(5, _ceil(daily * essential / 5)))                                           # B18

    yield_per_kwp = sum(m['days'] * m['yield'] for m in s['yield_profile'])                             # Assumptions!B22
    threshold = s['optimal_panel_share'] * s['panel_watts'] / 1000 * yield_per_kwp * s['block2_rate']  # B23
    sweep = _sweep(daily, day_share, battery_lead, export_ok, s)
    optimal = max([SWEEP_START] + [r['panels'] for r in sweep if r['marginal'] >= threshold and r['panels'] <= max_panels])  # B20
    panels = int(ov['panels']) if ov.get('panels') not in (None, '') else optimal                       # B22
    kwp = panels * s['panel_watts'] / 1000                                                               # B23

    lead = _monthly(kwp, daily, day_share, battery_lead, export_ok, s)
    other = _monthly(kwp, daily, day_share, battery_other, export_ok, s)
    totals = {k: sum(r[k] for r in lead) for k in ('consumption', 'generation', 'used', 'export', 'grid',
                                                   'bill_before', 'bill_after', 'saving')}
    saving_other = sum(r['saving'] for r in other)
    saving_split = totals['saving'] if lead_scope == 'split' else saving_other                            # B31
    saving_full = saving_other if lead_scope == 'split' else totals['saving']                             # B32

    supply = inputs.get('supply_phase') or 'Unknown'
    phase = (inputs.get('unknown_phase_as') or 'Three') if supply == 'Unknown' else supply

    ctx = {
        'phase': phase, 'kwp': kwp, 'panels': panels, 'lead_scope': lead_scope,
        'split_min_kw': split_min_kw, 'full_min_kw': full_min_kw,
        'battery_split': battery_split, 'battery_full': battery_full,
        'saving_split': saving_split, 'saving_full': saving_full,
    }
    options = [_option(tier, scope, ctx, catalogue, s)
               for scope in ('split', 'full') for tier in ('Tier 1', 'Tier 2')]

    return {
        'usage': {**usage, 'status_level': level, 'status': message},
        'sizing': {
            'daily': daily, 'daytime_share': day_share, 'essential_share': essential, 'max_panels': max_panels,
            'lead_scope': lead_scope, 'export_ok': export_ok, 'phase': phase,
            'battery_full_calc': battery_full_calc, 'battery_full': battery_full,
            'battery_split': battery_split, 'battery_lead': battery_lead,
            'full_min_kw': full_min_kw, 'split_min_kw': split_min_kw,
            'yield_per_kwp': yield_per_kwp, 'marginal_threshold': threshold,
            'optimal_panels': optimal, 'panels': panels, 'kwp': kwp,
            'annual_generation': totals['generation'], 'annual_used': totals['used'],
            'annual_export': totals['export'], 'annual_consumption': totals['consumption'],
            'share_from_solar': totals['used'] / totals['consumption'] if totals['consumption'] > 0 else 0.0,
            'current_annual_cost': totals['bill_before'],
            'saving_lead': totals['saving'], 'saving_split': saving_split, 'saving_full': saving_full,
        },
        'sweep': sweep,
        'monthly': lead,
        'options': options,
    }
