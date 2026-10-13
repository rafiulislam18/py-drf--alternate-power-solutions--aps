"""
Fixtures for the solar estimates app. The catalogue here is MADE UP (round
numbers) — real APS supplier pricing never goes into the repo; it is loaded
from the team's workbook instead (see test_workbook_parity.py).
"""

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.solar_estimates.defaults import ASSUMPTIONS, COMPANY
from apps.solar_estimates.models import CatalogueItem

CATALOGUE = [
    # category, code, tier, model, phase, kW, max PV kWp, usable kWh, max modules, cost
    ('inverter', '', 'Tier 1', 'T1 Controller 10 TP', 'Three', 10, 15, None, None, 30000),
    ('inverter', '', 'Tier 1', 'T1 Controller 15 TP', 'Three', 15, 22.5, None, None, 36000),
    ('inverter', '', 'Tier 1', 'T1 Controller 20 TP', 'Three', 20, 30, None, None, 45000),
    ('inverter', '', 'Tier 1', 'T1 Controller 8 SP', 'Single', 8, 16, None, None, 25000),
    ('inverter', '', 'Tier 2', 'T2 Hybrid 8K LP3', 'Three', 8, 10.4, None, None, 18000),
    ('inverter', '', 'Tier 2', 'T2 Hybrid 10K LP3', 'Three', 10, 13, None, None, 20000),
    ('inverter', '', 'Tier 2', 'T2 Hybrid 15K LP3', 'Three', 15, 24, None, None, 27000),
    ('inverter', '', 'Tier 2', 'T2 Hybrid 20K LP3', 'Three', 20, 26, None, None, 36000),
    ('inverter', '', 'Tier 2', 'T2 Hybrid 8K LP1', 'Single', 8, 10.4, None, None, 15000),
    ('battery', '', 'Tier 1', 'T1 Battery 9.0', 'Any', None, None, 8.8, 6, 24000),
    ('battery', '', 'Tier 2', 'T2 Battery 5.12', 'Any', None, None, 4.6, 16, 14000),
    ('panel', 'panel', 'Any', '620 W module', 'Any', None, None, None, None, 1500),
    ('bos', 'mounting', 'Any', 'Mounting', 'Any', None, None, None, None, 450),
    ('bos', 'dc_kit', 'Any', 'DC kit', 'Any', None, None, None, None, 3600),
    ('bos', 'ac_split', 'Any', 'AC split', 'Any', None, None, None, None, 6300),
    ('bos', 'ac_full', 'Any', 'AC full', 'Any', None, None, None, None, 13500),
    ('bos', 'gateway_t1', 'Any', 'Gateway', 'Any', None, None, None, None, 11700),
    ('bos', 'cabinet_t2', 'Any', 'Cabinet', 'Any', None, None, None, None, 5400),
    ('bos', 'cabling', 'Any', 'Cabling', 'Any', None, None, None, None, 360),
    ('bos', 'labour', 'Any', 'Labour', 'Any', None, None, None, None, 810),
    ('bos', 'coc', 'Any', 'COC', 'Any', None, None, None, None, 720),
    ('bos', 'sseg', 'Any', 'SSEG', 'Any', None, None, None, None, 3600),
    ('bos', 'transport', 'Any', 'Transport', 'Any', None, None, None, None, 2400),
]


def engine_catalogue():
    keys = ['category', 'code', 'tier', 'model', 'phase', 'rated_kw', 'max_pv_kwp', 'usable_kwh', 'max_modules', 'cost']
    return [dict(zip(keys, row), brand='') for row in CATALOGUE]


def engine_settings(**over):
    return {**ASSUMPTIONS, **COMPANY, **over}


@pytest.fixture
def catalogue(db):
    for i, (cat, code, tier, model, phase, kw, pv, usable, maxmod, cost) in enumerate(CATALOGUE):
        CatalogueItem.objects.create(category=cat, code=code, tier=tier, model=model, phase=phase, rated_kw=kw,
                                     max_pv_kwp=pv, usable_kwh=usable, max_modules=maxmod, cost_excl_vat=cost,
                                     sort_order=i)


@pytest.fixture
def staff(db):
    api = APIClient()
    api.force_authenticate(User.objects.create_user('ops', is_staff=True, is_superuser=True))  # full staff access
    return api


def inputs(**over):
    base = {
        'client_name': 'Test Trust', 'site_address': '1 Main Road, Wynberg', 'erf_number': '123',
        'contact_person': 'Lerato', 'phone': '082 555 0100', 'email': 'lerato@ex.example',
        'supply_phase': 'Three', 'main_breaker_a': 100, 'estimate_date': '2026-10-08',
        'readings': [
            {'date': '2026-01-10', 'reading': 10000, 'type': 'Actual'},
            {'date': '2026-03-15', 'reading': 14000, 'type': 'Estimate'},
            {'date': '2026-08-06', 'reading': 25705, 'type': 'Actual'},
        ],
    }
    base.update(over)
    return base
