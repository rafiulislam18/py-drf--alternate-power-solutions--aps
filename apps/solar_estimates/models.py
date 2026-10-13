"""
Solar & battery budget estimates (staff dashboard → Sales → Solar estimates).

- EstimateSettings: one row of company-wide assumptions (tariffs, yield,
  margin, VAT number …) — the workbook's Assumptions tab.
- CatalogueItem: inverters, battery modules, the panel and balance-of-system
  lines with their COST excl VAT — the workbook's Catalogue tab. Sell prices
  are derived (cost ÷ (1 − margin)), never stored.
- SolarEstimate: one estimate — the form inputs, the staff's edits on the
  preview and, once confirmed, a frozen snapshot of the document so a later
  price or tariff change never alters an issued estimate.
"""

from django.conf import settings
from django.db import models

from . import defaults


def _default_yield():
    return [dict(m) for m in defaults.YIELD_PROFILE]


class EstimateSettings(models.Model):
    block1_rate = models.FloatField(default=defaults.ASSUMPTIONS['block1_rate'], help_text='R/kWh incl VAT')
    block2_rate = models.FloatField(default=defaults.ASSUMPTIONS['block2_rate'], help_text='R/kWh incl VAT')
    block1_threshold = models.FloatField(default=defaults.ASSUMPTIONS['block1_threshold'], help_text='kWh per month')
    service_charge = models.FloatField(default=defaults.ASSUMPTIONS['service_charge'], help_text='R/month incl VAT')
    export_credit = models.FloatField(default=defaults.ASSUMPTIONS['export_credit'], help_text='R/kWh')
    battery_efficiency = models.FloatField(default=defaults.ASSUMPTIONS['battery_efficiency'])
    daytime_share = models.FloatField(default=defaults.ASSUMPTIONS['daytime_share'])
    battery_night_target = models.FloatField(default=defaults.ASSUMPTIONS['battery_night_target'])
    optimal_panel_share = models.FloatField(default=defaults.ASSUMPTIONS['optimal_panel_share'])
    panel_watts = models.FloatField(default=defaults.ASSUMPTIONS['panel_watts'])
    sweep_step = models.PositiveSmallIntegerField(default=defaults.ASSUMPTIONS['sweep_step'])
    vat_rate = models.FloatField(default=defaults.ASSUMPTIONS['vat_rate'])
    prudence_factor = models.FloatField(default=defaults.ASSUMPTIONS['prudence_factor'])
    target_margin = models.FloatField(default=defaults.ASSUMPTIONS['target_margin'])
    yield_profile = models.JSONField(default=_default_yield)
    tariff_increase = models.FloatField(default=defaults.ASSUMPTIONS['tariff_increase'],
                                        help_text='Annual tariff increase for payback and 10-year figures, e.g. 0.10')
    panel_degradation = models.FloatField(default=defaults.ASSUMPTIONS['panel_degradation'],
                                          help_text='Annual panel output loss, e.g. 0.005')
    price_rounding = models.CharField(max_length=12, choices=defaults.ROUNDING_CHOICES,
                                      default=defaults.ASSUMPTIONS['price_rounding'])

    company_vat_number = models.CharField(max_length=40, blank=True, default=defaults.COMPANY['company_vat_number'])
    company_phone = models.CharField(max_length=40, blank=True, default=defaults.COMPANY['company_phone'])
    company_address = models.CharField(max_length=200, blank=True, default=defaults.COMPANY['company_address'])
    company_email = models.CharField(max_length=120, blank=True, default=defaults.COMPANY['company_email'])
    quote_prefix = models.CharField(max_length=20, default=defaults.COMPANY['quote_prefix'])
    quote_counter_reset = models.CharField(max_length=8, choices=defaults.COUNTER_RESET_CHOICES,
                                           default=defaults.COMPANY['quote_counter_reset'])
    default_prepared_by = models.CharField(max_length=150, blank=True, default=defaults.COMPANY['default_prepared_by'])
    validity_days = models.PositiveSmallIntegerField(default=defaults.COMPANY['validity_days'])
    deposit_percent = models.FloatField(null=True, blank=True, default=defaults.COMPANY['deposit_percent'],
                                        help_text='e.g. 70 for 70%; blank prints "[confirm]"')
    balance_terms = models.CharField(max_length=200, blank=True, default=defaults.COMPANY['balance_terms'])
    updated_at = models.DateTimeField(auto_now=True)

    ENGINE_FIELDS = list(defaults.ASSUMPTIONS)

    class Meta:
        verbose_name = 'Estimate settings'
        verbose_name_plural = 'Estimate settings'

    def __str__(self):
        return 'Solar estimate settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def as_engine_dict(self):
        return {f: getattr(self, f) for f in self.ENGINE_FIELDS}


class CatalogueItem(models.Model):
    class Category(models.TextChoices):
        INVERTER = 'inverter', 'Inverter'
        BATTERY = 'battery', 'Battery module'
        PANEL = 'panel', 'Solar panel'
        BOS = 'bos', 'Balance of system & services'

    TIERS = [('Tier 1', 'Tier 1 (Sigenergy)'), ('Tier 2', 'Tier 2 (Deye)'), ('Any', 'Any')]
    PHASES = [('Single', 'Single'), ('Three', 'Three'), ('Any', 'Any')]

    category = models.CharField(max_length=10, choices=Category.choices)
    code = models.CharField(max_length=20, blank=True,
                            help_text='Balance-of-system line: mounting, dc_kit, ac_split, ac_full, gateway_t1, '
                                      'cabinet_t2, cabling, labour, coc, sseg, transport')
    tier = models.CharField(max_length=6, choices=TIERS, default='Any')
    brand = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=150)
    phase = models.CharField(max_length=6, choices=PHASES, default='Any')
    rated_kw = models.FloatField(null=True, blank=True)
    max_pv_kwp = models.FloatField(null=True, blank=True)
    usable_kwh = models.FloatField(null=True, blank=True, help_text='Battery: usable kWh per module')
    max_modules = models.PositiveSmallIntegerField(null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    cost_excl_vat = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                        help_text='Blank = not priced yet ("Price TBC" on the estimate)')
    notes = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.get_category_display()}: {self.model}'

    def as_engine_dict(self):
        return {
            'id': self.pk, 'category': self.category, 'code': self.code, 'tier': self.tier, 'brand': self.brand,
            'model': self.model, 'phase': self.phase, 'rated_kw': self.rated_kw, 'max_pv_kwp': self.max_pv_kwp,
            'usable_kwh': self.usable_kwh, 'max_modules': self.max_modules,
            'cost': float(self.cost_excl_vat) if self.cost_excl_vat is not None else None,
        }


class SolarEstimate(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ISSUED = 'issued', 'Confirmed'

    number = models.CharField(max_length=40, blank=True, db_index=True)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.DRAFT)
    client_name = models.CharField(max_length=200, blank=True)
    site_address = models.CharField(max_length=300, blank=True)
    inputs = models.JSONField(default=dict)
    edits = models.JSONField(default=dict, blank=True)
    # The document as confirmed (display values), so the PDF never changes later.
    snapshot = models.JSONField(null=True, blank=True)
    # HelioScope (or similar) roof layout shown in section 3 of the proposal.
    roof_image = models.ImageField(upload_to='solar_estimates/roofs/', blank=True)
    total_incl_vat = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                         help_text='Recommended option, for the list')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='solar_estimates')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['number'], condition=~models.Q(number=''), name='solar_estimate_number_unique'),
        ]

    def __str__(self):
        return self.number or f'Draft estimate #{self.pk}'
