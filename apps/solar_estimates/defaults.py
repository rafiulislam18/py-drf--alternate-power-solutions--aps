"""
Default assumptions and the fixed wording of the solar & battery estimate.

The numbers mirror the "Assumptions" tab of APS_Solar_Estimate_TEMPLATE.xlsx
(City of Cape Town 2026/27 Home User tariffs, Cape Town PV yield). They are
only the starting values: staff change them on the estimates settings page,
or by uploading an updated workbook.

Equipment costs are NOT here on purpose — they are supplier pricing and live
in the database (CatalogueItem), loaded from the workbook's Catalogue tab.
"""

# (month, days, PV yield in kWh per kWp per day) — north-facing, ~30° tilt.
YIELD_PROFILE = [
    {'month': 'Jan', 'days': 31, 'yield': 6.0},
    {'month': 'Feb', 'days': 28, 'yield': 5.7},
    {'month': 'Mar', 'days': 31, 'yield': 4.9},
    {'month': 'Apr', 'days': 30, 'yield': 3.9},
    {'month': 'May', 'days': 31, 'yield': 3.0},
    {'month': 'Jun', 'days': 30, 'yield': 2.6},
    {'month': 'Jul', 'days': 31, 'yield': 2.8},
    {'month': 'Aug', 'days': 31, 'yield': 3.5},
    {'month': 'Sep', 'days': 30, 'yield': 4.4},
    {'month': 'Oct', 'days': 31, 'yield': 5.3},
    {'month': 'Nov', 'days': 30, 'yield': 5.8},
    {'month': 'Dec', 'days': 31, 'yield': 6.1},
]

ASSUMPTIONS = {
    'block1_rate': 3.5595,          # R/kWh incl VAT (R3.0952 excl)
    'block2_rate': 4.6906,          # R/kWh incl VAT (R4.0788 excl)
    'block1_threshold': 600,        # kWh per month, prorated by days
    'service_charge': 424.30,       # R/month incl VAT — unaffected by solar
    'export_credit': 1.53,          # R/kWh SSEG credit (only if export permitted)
    'battery_efficiency': 0.92,     # round trip
    'daytime_share': 0.40,          # default share of daily use in daytime
    'battery_night_target': 0.80,   # battery covers this share of night use
    'optimal_panel_share': 0.50,    # next pair must earn >= this share of a fully used panel
    'panel_watts': 620,
    'sweep_step': 2,
    'vat_rate': 0.15,
    'prudence_factor': 0.90,        # client-facing savings are reduced by this
    'target_margin': 0.10,          # sell = cost / (1 - margin)
    'yield_profile': YIELD_PROFILE,
    # Proposal & Quotation format (10/2026): payback and 10-year figures grow
    # each year's saving by the tariff increase, less panel ageing.
    'tariff_increase': 0.10,        # per year
    'panel_degradation': 0.005,     # per year
    # Price rounding: 'item' = each item's sell price to whole rands (the
    # workbook); 'exact' = cents kept; 'nearest_10' / 'nearest_100' = exact
    # items, total excl VAT rounded to the nearest R10 / R100.
    'price_rounding': 'item',
}
ROUNDING_CHOICES = [
    ('item', 'Round each item to whole rands (workbook)'),
    ('exact', 'Keep exact cents'),
    ('nearest_10', 'Round the total excl VAT to the nearest R10'),
    ('nearest_100', 'Round the total excl VAT to the nearest R100'),
]

COMPANY = {
    'company_vat_number': '4480272055',
    'company_phone': '068 319 3323',
    'company_address': 'Unit 8, Witkin Building, 1 Lester Rd, Wynberg',
    'company_email': 'info@alter-power.co.za',
    'default_prepared_by': 'Sediek Sadien, Operations, APS',
    'validity_days': 7,
    'deposit_percent': 70,
    'balance_terms': 'Balance on completion & commissioning',
    # Quotation number: <prefix>-<YYMM>-<SITE>-<NNN>, NNN counting up and
    # starting again each month ('monthly') or never ('never').
    'quote_prefix': 'APS-PRJ',
    'quote_counter_reset': 'monthly',
}
COUNTER_RESET_CHOICES = [('monthly', 'Start again at 001 each month'), ('never', 'Never reset')]

DEFAULT_ASSESSMENT = ('Desktop assessment from municipal meter readings and a HelioScope roof layout. A site visit '
                      'confirms the design before installation.')

# Balance-of-system lines, in the order they appear in the Options tab.
# 'per': how the catalogue price is multiplied — per system, per panel or per kWp.
BOS_LINES = [
    ('mounting', 'Mounting & rails', 'panel'),
    ('dc_kit', 'DC protection kit', 'system'),
    ('ac', 'AC protection, DB & changeover', 'system'),        # ac_split or ac_full by scope
    ('enclosure', 'Sigen Gateway / battery cabinet', 'system'),  # gateway_t1 or cabinet_t2 by tier
    ('cabling', 'Cabling & sundries', 'kwp'),
    ('labour', 'Installation labour', 'kwp'),
    ('coc', 'Electrical COC', 'system'),
    ('sseg', 'CoCT SSEG application & sign-off', 'system'),
    ('transport', 'Transport & P&G', 'system'),
]

BOS_CODES = {
    'mounting': 'Mounting & rails (per panel)',
    'dc_kit': 'DC isolators, SPDs, fusing, combiner',
    'ac_split': 'Smart split: essential / non-essential DBs, changeover & AC protection',
    'ac_full': 'Full cut-over: main changeover, bypass & AC protection',
    'gateway_t1': 'Sigen Gateway (backup switching) – Tier 1 only',
    'cabinet_t2': 'Battery rack / weatherproof cabinet – Tier 2 only',
    'cabling': 'Cabling, trunking & sundries (per kWp)',
    'labour': 'Installation labour (per kWp)',
    'coc': 'Electrical COC (SANS 10142-1)',
    'sseg': 'CoCT SSEG application & engineer sign-off',
    'transport': 'Transport, access & P&G',
}

TIERS = [
    ('Tier 1', 'Sigenergy', 'premium all-in-one stackable system, weather-rated, app monitoring'),
    ('Tier 2', 'Deye', 'proven, cost-effective hybrid inverter with modular LFP batteries'),
]

HEADER_ADDRESS = 'Wynberg, Cape Town  ·  info@alter-power.co.za  ·  sales@alter-power.co.za  ·  www.alter-power.co.za'
TAGLINE = 'Alternate Power Solutions (Pty) Ltd  ·  Established 2015  ·  1000+ projects across Cape Town'

SPLIT_INTRO = (
    'We recommend the Smart split: your existing DBs are re-arranged into an essential (backed-up) board and a '
    'non-essential board. Solar still reduces the bill for the whole house; during an outage the essential circuits '
    'stay on, while heavy loads such as underfloor heating, geysers and ovens wait for the grid. This needs a smaller '
    'inverter and battery, so it costs less. A full cut-over of the whole house is also priced if you prefer.'
)
FULL_INTRO = (
    'We have priced a full cut-over, so the whole house stays on during an outage. A Smart split (essential circuits '
    'only) is also shown as a lower-cost alternative.'
)

SCOPE_OF_WORK = [
    'Site assessment, roof layout and final system design.',
    'Supply and install inverter, battery storage, solar panels and mounting as per the selected option.',
    'DC and AC protection (isolators, surge protection, fusing); essential / non-essential DB split or full '
    'changeover, as per the selected option.',
    'Cabling, trunking, earthing and commissioning; monitoring app set-up and client hand-over.',
    'Electrical Certificate of Compliance and City of Cape Town SSEG registration documentation.',
]
EXCLUSIONS = [
    'Roof repairs, waterproofing or structural reinforcement.',
    'Municipal fees, meter changes or upgrades required by the City.',
    'DB upgrades or rewiring beyond the essential-load circuit, unless quoted.',
    'Any existing inverter/battery backup on site, unless stated as included.',
    "Builder's work, trenching or scaffolding unless stated.",
    'Pricing assumes normal access and standard working hours.',
]
COMPLIANCE = [
    'Installation to SANS 10142-1 (wiring of premises) with Certificate of Compliance.',
    'Embedded generation per SANS 10142-1-2; inverters on the NRS 097-2-1 approved list.',
    'Grid-tied systems registered with the City of Cape Town (SSEG) before commissioning.',
]
NEXT_STEP = (
    'Reply to this estimate or call us to book a free site assessment. We will confirm your supply phase, roof layout '
    'and peak load, then issue a firm quotation.'
)


# ── Proposal & Quotation wording (22 Chateau Circle format, 10/2026) ────────

SIGEN_WARRANTY = [
    ('10-year warranty:', 'manufacturer warranty on the Sigen inverter (Energy Controller) and Sigen batteries.'),
    ('On-site swap-out:', 'where a hardware fault is confirmed, Sigenergy arranges an on-site replacement of the unit '
                          'through its local service partner – the faulty unit is swapped at your home rather than '
                          'removed for workshop repair.'),
    ('Covered:', 'the replacement hardware (new, refurbished or upgraded, at least functionally equivalent), the '
                 'on-site labour to remove and reinstall it, and shipping of the parts.'),
    ('Battery performance:', 'the batteries retain at least 70% of their usable energy over 10 years.'),
    ('Remote support:', 'monitoring and fault-finding through the mySigen app; many issues are resolved remotely with '
                        'a firmware update.'),
]
SIGEN_CONDITIONS = (
    'Conditions (Sigenergy Factory Limited Warranty): the system must stay connected to the internet for firmware '
    'updates to keep the full 10-year cover (5 years otherwise); claims are logged via the mySigen app within 20 days '
    'of the fault; the service partner’s travel and accommodation are not covered; not valid within 500 m of the '
    'coastline; the Sigen Energy Gateway carries a 2-year warranty.'
)
INVESTMENT_NOTE = (
    'Each price includes the equipment shown in section 4; solar panels and mounting; DC and AC protection; the '
    'essential / non-essential DB split or full changeover; cabling, installation labour, programming and '
    'commissioning; the Certificate of Compliance; and the City of Cape Town SSEG application with engineer sign-off. '
    'Payment terms are set out in section 9.'
)
PROPOSAL_SCOPE = [
    ('Included:', 'site assessment and final system design; supply and delivery of the inverter, batteries, solar '
                  'panels and mounting for the selected option; DC and AC protection (isolators, surge protection, '
                  'fusing); the essential / non-essential DB split or full changeover; cabling, trunking and earthing; '
                  'installation, programming and commissioning; monitoring app set-up; City of Cape Town SSEG '
                  'application and engineer sign-off; CoC; handover.'),
    ('Excluded:', 'roof repairs, waterproofing or structural reinforcement; City fees, meter changes or upgrades '
                  'required by the City; DB upgrades or rewiring beyond the essential-load circuits unless quoted; '
                  'builder’s work, trenching or scaffolding; work outside normal access and working hours.'),
    ('Compliance:', 'installation to SANS 10142-1 (wiring of premises) and SANS 10142-1-2 (embedded generation); '
                    'inverters on the NRS 097-2-1 approved list; roof loading in line with SANS 10160; City SSEG '
                    'registration before commissioning; CoC issued on completion.'),
]
TIMELINE = [
    ('Site assessment', 'Booked on acceptance of this proposal; confirms supply, roof layout and peak load'),
    ('City application', 'Submitted on acceptance; runs in parallel'),
    ('Installation', 'About 4–5 working days after deposit and equipment delivery'),
    ('Commissioning and handover', 'On completion, with the CoC issued'),
]
CLOSING = (
    'APS is Cape Town-based since 2015 and handles the design, electrical work and after-sales support as one team. '
    'To proceed, select your option and sign the acceptance below, and we will book the site assessment.'
)
