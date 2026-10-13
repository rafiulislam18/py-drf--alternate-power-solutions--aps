"""
The sizing engine (apps.solar_estimates.engine) — a copy of the APS workbook:
readings → daily use, tariff blocks, the panel sweep, inverter / battery
selection, pricing with margin and VAT, scopes, overrides, zero export.
"""

import pytest

from apps.solar_estimates import engine
from apps.solar_estimates.engine import calculate, consumption_from_readings, sell_price

from .conftest import engine_catalogue, engine_settings, inputs


def calc(**over):
    return calculate(inputs(**over), engine_settings(), engine_catalogue())


def opt(result, key):
    return next(o for o in result['options'] if o['key'] == key)


# ── readings ─────────────────────────────────────────────────────────────────

def test_daily_use_from_first_and_last_actual_readings_only():
    usage = consumption_from_readings(inputs()['readings'])
    assert usage['days'] == 208 and usage['kwh_used'] == 15705
    assert usage['daily'] == pytest.approx(75.505, abs=0.001)


@pytest.mark.parametrize('days,level', [(10, 'error'), (19, 'error'), (20, 'ok'), (59, 'ok'), (60, 'good')])
def test_data_status(days, level):
    assert engine.data_status(days)[0] == level


def test_too_few_readings_says_so_but_an_override_still_sizes():
    r = calc(readings=[{'date': '2026-01-01', 'reading': 100, 'type': 'Actual'}])
    assert r['usage']['status'] == 'Need at least 2 actual readings ≥20 days apart'
    r = calc(readings=[], overrides={'daily_kwh': 40})
    assert r['sizing']['daily'] == 40


# ── prices and tariffs ───────────────────────────────────────────────────────

def test_sell_price_is_cost_over_one_minus_margin_rounded_half_up():
    assert sell_price(1566, 0.10) == 1740
    assert sell_price(0.45, 0.10) == 1          # .5 rounds up like Excel, not to even
    assert sell_price(None, 0.10) == 0


def test_monthly_bill_uses_prorated_block_threshold_and_service_charge():
    r = calc(overrides={'daily_kwh': 30, 'panels': 4})
    jan = r['monthly'][0]
    assert jan['days'] == 31 and jan['consumption'] == 930
    assert jan['bill_before'] == pytest.approx(4103.02, abs=0.01)


# ── sizing ───────────────────────────────────────────────────────────────────

def test_sweep_picks_the_largest_count_whose_marginal_value_clears_the_threshold():
    r = calc()
    s = r['sizing']
    good = [row['panels'] for row in r['sweep'] if row['marginal'] >= s['marginal_threshold']]
    assert s['optimal_panels'] == max(good) and s['panels'] == s['optimal_panels']
    assert s['marginal_threshold'] == pytest.approx(2384.99, abs=0.01)
    assert s['kwp'] == pytest.approx(s['panels'] * 0.62)
    assert s['annual_generation'] == pytest.approx(s['kwp'] * 1640.2)


def test_roof_limit_and_panel_override():
    assert calc(max_panels=10)['sizing']['panels'] <= 10
    assert calc(overrides={'panels': 14})['sizing']['panels'] == 14


def test_inverter_is_the_smallest_fitting_unit_for_tier_and_phase():
    r = calc()                                     # ~75.5 kWh/day, three-phase
    assert r['sizing']['split_min_kw'] == 8        # ROUNDUP(75.5 × 0.5 ÷ 5)
    assert r['sizing']['full_min_kw'] == 16        # ROUNDUP(75.5 ÷ 5)
    assert opt(r, 't2_full')['inverter']['model'] == 'T2 Hybrid 20K LP3'
    assert opt(r, 't1_full')['inverter']['model'] == 'T1 Controller 20 TP'
    # split: ≥ 8 kW and enough PV input for the array
    assert opt(r, 't2_split')['inverter']['max_pv_kwp'] >= r['sizing']['kwp']


def test_inverter_overrides_and_no_fit():
    r = calc(overrides={'split_min_inverter_kw': 15})
    assert opt(r, 't2_split')['inverter']['model'] == 'T2 Hybrid 15K LP3'
    r = calc(overrides={'full_min_inverter_kw': 40})
    assert opt(r, 't1_full')['inverter'] is None
    assert opt(r, 't1_full')['unpriced'] >= 1


def test_battery_modules_round_up_with_the_095_factor_and_cap():
    r = calc()
    target = r['sizing']['battery_full']           # daily × night share × 0.8
    assert target == pytest.approx(r['sizing']['daily'] * 0.6 * 0.8)
    t2 = opt(r, 't2_full')['battery']
    assert t2['qty'] == engine._ceil(target * 0.95 / 4.6)
    assert opt(r, 't2_split')['battery']['qty'] == engine._ceil(target * 0.5 * 0.95 / 4.6)
    assert opt(calc(overrides={'battery_kwh': 500}), 't1_full')['battery']['qty'] == 6   # max modules


def test_option_pricing_lines_vat_and_payback():
    r = calc(overrides={'panels': 20})
    o = opt(r, 't2_split')
    lines = {line['label']: line['amount'] for line in o['lines']}
    assert lines['Solar panels'] == 20 * 1667             # 1500 ÷ 0.9
    assert lines['Mounting & rails'] == 20 * 500
    assert lines['Cabling & sundries'] == pytest.approx(12.4 * 400)
    assert lines['AC protection, DB & changeover'] == 7000
    assert lines['Sigen Gateway / battery cabinet'] == 6000  # Tier 2 → cabinet
    assert opt(r, 't1_split')['lines'][6]['amount'] == 13000  # Tier 1 → gateway
    assert opt(r, 't2_full')['lines'][5]['amount'] == 15000   # full cut-over AC
    assert o['subtotal'] == pytest.approx(sum(lines.values()))
    assert o['total'] == pytest.approx(o['subtotal'] * 1.15)
    assert o['saving_client'] == pytest.approx(o['saving_model'] * 0.9)
    assert o['payback_simple'] == pytest.approx(o['total'] / o['saving_client'])
    assert o['payback_years'] < o['payback_simple']          # savings grow with the tariff increase


def test_smart_split_and_full_cut_over_savings_and_lead_scope():
    split = calc(overrides={'panels': 20})
    assert split['sizing']['lead_scope'] == 'split'
    assert split['sizing']['saving_full'] > split['sizing']['saving_split'] > 0   # bigger battery stores more
    assert [o['recommended'] for o in split['options']] == [True, True, False, False]
    full = calc(backup_scope='full', overrides={'panels': 20})
    assert [o['recommended'] for o in full['options']] == [False, False, True, True]
    assert full['sizing']['saving_split'] == pytest.approx(split['sizing']['saving_split'])
    assert full['sizing']['saving_full'] == pytest.approx(split['sizing']['saving_full'])


def test_zero_export_counts_no_credit():
    r = calc(export_to_grid=False, overrides={'panels': 30})
    assert r['sizing']['annual_export'] == 0
    assert calc(overrides={'panels': 30})['sizing']['saving_lead'] > r['sizing']['saving_lead']


def test_unknown_phase_is_priced_as_chosen():
    r = calc(supply_phase='Unknown', unknown_phase_as='Single', overrides={'daily_kwh': 20, 'panels': 8})
    assert r['sizing']['phase'] == 'Single'
    assert opt(r, 't2_split')['inverter']['model'] == 'T2 Hybrid 8K LP1'


def test_missing_cost_leaves_the_line_unpriced():
    cat = engine_catalogue()
    next(c for c in cat if c['code'] == 'coc')['cost'] = None
    r = calculate(inputs(), engine_settings(), cat)
    assert all(o['unpriced'] == 1 for o in r['options'])


def test_same_inputs_same_numbers():
    assert calc() == calc()


# ── price rounding modes and escalated returns (Proposal & Quotation) ────────

def test_rounding_modes():
    assert sell_price(1566, 0.10, 'item') == 1740
    assert sell_price(1566, 0.10, 'exact') == pytest.approx(1740.0)
    assert sell_price(1000, 0.30, 'exact') == pytest.approx(1428.5714, abs=1e-4)
    assert sell_price(1000, 0.30, 'item') == 1429
    assert engine.round_total(123_456.78, 'nearest_10') == 123_460
    assert engine.round_total(123_456.78, 'nearest_100') == 123_500
    assert engine.round_total(123_450, 'nearest_100') == 123_500      # half up
    assert engine.round_total(123_456.78, 'item') == 123_456.78


@pytest.mark.parametrize('mode', ['item', 'exact', 'nearest_10', 'nearest_100'])
def test_option_totals_follow_the_rounding_setting(mode):
    r = calculate(inputs(overrides={'panels': 14}), engine_settings(target_margin=0.3, price_rounding=mode), engine_catalogue())
    sub = opt(r, 't2_split')['subtotal']
    if mode == 'nearest_10':
        assert sub % 10 == 0
    elif mode == 'nearest_100':
        assert sub % 100 == 0
    lines = {line['label']: line['amount'] for line in opt(r, 't2_split')['lines']}
    if mode == 'item':
        assert lines['Solar panels'] == 14 * 2143                        # R 1,500 ÷ 0.7 = 2,142.86 → whole rands
    else:
        assert lines['Solar panels'] == pytest.approx(14 * 1500 / 0.7)   # cents kept per item


def test_escalated_returns_match_the_proposal_formula():
    # 22 Chateau Circle, Deye smart split: R 52,657.45 year-1, R 168,504.84 incl VAT.
    back = engine.returns(52657.45, 168504.84, 0.10, 0.005)
    assert back['growth'] == pytest.approx(1.10 * 0.995)
    assert round(back['payback_years'], 1) == 2.9
    assert round(back['ten_year_saving']) == 817_408
    assert round(back['net_10y']) == 648_903
    assert round(back['first_year_return'] * 100) == 31
    # payback interpolates the last part-year
    flat = engine.returns(100, 250, 0, 0)
    assert flat['payback_years'] == pytest.approx(2.5) and flat['ten_year_saving'] == pytest.approx(1000)
    assert engine.returns(0, 1000, 0.1, 0.005)['payback_years'] is None
