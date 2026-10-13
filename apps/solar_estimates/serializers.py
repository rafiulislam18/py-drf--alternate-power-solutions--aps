import re

from rest_framework import serializers

from .models import CatalogueItem, EstimateSettings, SolarEstimate


class ReadingSerializer(serializers.Serializer):
    date = serializers.DateField()
    reading = serializers.FloatField(min_value=0)
    type = serializers.ChoiceField(choices=['Actual', 'Estimate'], default='Actual')
    source = serializers.CharField(max_length=120, allow_blank=True, required=False, default='')


class OverridesSerializer(serializers.Serializer):
    daily_kwh = serializers.FloatField(min_value=0.1, max_value=5000, required=False, allow_null=True)
    panels = serializers.IntegerField(min_value=1, max_value=400, required=False, allow_null=True)
    battery_kwh = serializers.FloatField(min_value=0, max_value=1000, required=False, allow_null=True)
    full_min_inverter_kw = serializers.FloatField(min_value=1, max_value=200, required=False, allow_null=True)
    split_min_inverter_kw = serializers.FloatField(min_value=1, max_value=200, required=False, allow_null=True)


def _text(max_length=200):
    return serializers.CharField(max_length=max_length, allow_blank=True, required=False, default='')


class EstimateInputSerializer(serializers.Serializer):
    """Everything typed on the estimate form (the workbook's Input tab)."""

    # 1. estimate details — estimate_number = the quotation number; blank = given on confirm
    estimate_number = _text(40)
    site_code = serializers.RegexField(r'^[A-Za-z0-9]{0,6}$', max_length=6, required=False, allow_blank=True, default='',
                                       error_messages={'invalid': 'Site code: letters and digits only, up to 6.'})
    estimate_date = serializers.DateField(required=False, allow_null=True)
    validity_days = serializers.IntegerField(min_value=1, max_value=365, required=False, allow_null=True)
    prepared_by = _text(150)
    # 2. client & property
    client_name = _text()
    contact_person = _text(150)
    phone = _text(40)
    email = serializers.EmailField(allow_blank=True, required=False, default='')
    client_type = _text(60)
    site_address = _text(300)
    erf_number = _text(40)
    municipal_account = _text(60)
    tariff = serializers.CharField(max_length=60, required=False, default='Home User')
    # 3. site information
    supply_phase = serializers.ChoiceField(choices=['Single', 'Three', 'Unknown'], default='Unknown')
    supply_to_be_confirmed = serializers.BooleanField(default=False)
    assessment = _text(500)
    unknown_phase_as = serializers.ChoiceField(choices=['Single', 'Three'], default='Three')
    main_breaker_a = serializers.IntegerField(min_value=1, max_value=2000, required=False, allow_null=True)
    roof_type = _text(60)
    orientation = _text(60)
    max_panels = serializers.IntegerField(min_value=4, max_value=400, default=60)
    # Roof layout (e.g. HelioScope): shown on the proposal, separate from the sizing panel.
    roof_max_panels = serializers.IntegerField(min_value=1, max_value=1000, required=False, allow_null=True)
    roof_panel_watts = serializers.IntegerField(min_value=100, max_value=1000, required=False, allow_null=True)
    shading = _text(120)
    client_priority = _text(60)
    existing_backup = _text(120)
    existing_backup_details = _text(300)
    backup_scope = serializers.ChoiceField(choices=['split', 'full'], default='split')
    essential_share = serializers.FloatField(min_value=0.05, max_value=1, default=0.5)
    export_to_grid = serializers.BooleanField(default=True)
    daytime_share_override = serializers.FloatField(min_value=0.05, max_value=1, required=False, allow_null=True)
    site_notes = _text(2000)
    # 4. sizing overrides
    overrides = OverridesSerializer(required=False, default=dict)
    # 5. commercial
    deposit_percent = serializers.FloatField(min_value=0, max_value=100, required=False, allow_null=True)
    balance_terms = _text(200)
    # 6. municipal bill readings
    readings = ReadingSerializer(many=True, required=False, default=list)

    def to_engine(self):
        """Validated data as plain JSON (dates as ISO strings) for storage and the engine."""
        data = dict(self.validated_data)
        data['readings'] = [{**r, 'date': r['date'].isoformat()} for r in data.get('readings', [])]
        if data.get('estimate_date'):
            data['estimate_date'] = data['estimate_date'].isoformat()
        data['overrides'] = {k: v for k, v in dict(data.get('overrides') or {}).items() if v is not None}
        return data


class EditsField(serializers.DictField):
    """Preview edits: field key → shown text (or a number for prices/savings)."""

    def __init__(self, **kw):
        super().__init__(child=serializers.CharField(max_length=2000, allow_blank=True), required=False,
                         default=dict, **kw)

    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        if len(data) > 200:
            raise serializers.ValidationError('Too many edits.')
        return data


class SettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = EstimateSettings
        exclude = ['id']
        read_only_fields = ['updated_at']

    def validate_yield_profile(self, value):
        if not isinstance(value, list) or len(value) != 12:
            raise serializers.ValidationError('Twelve months are needed.')
        for m in value:
            try:
                if not (1 <= int(m['days']) <= 31) or float(m['yield']) < 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise serializers.ValidationError('Each month needs days (1–31) and a yield (kWh/kWp/day).')
        return [{'month': str(m.get('month', ''))[:3], 'days': int(m['days']), 'yield': float(m['yield'])} for m in value]

    def validate(self, attrs):
        for field in ('battery_efficiency', 'daytime_share', 'battery_night_target', 'optimal_panel_share',
                      'vat_rate', 'prudence_factor'):
            if field in attrs and not (0 < attrs[field] <= 1):
                raise serializers.ValidationError({field: 'Must be a fraction between 0 and 1 (e.g. 0.92).'})
        if 'target_margin' in attrs and not (0 <= attrs['target_margin'] < 1):
            raise serializers.ValidationError({'target_margin': 'Must be between 0 and 0.99 (e.g. 0.10 for 10%).'})
        if 'tariff_increase' in attrs and not (0 <= attrs['tariff_increase'] <= 1):
            raise serializers.ValidationError({'tariff_increase': 'Must be a fraction between 0 and 1 (e.g. 0.10 for 10%).'})
        if 'panel_degradation' in attrs and not (0 <= attrs['panel_degradation'] < 0.2):
            raise serializers.ValidationError({'panel_degradation': 'Must be a small fraction (e.g. 0.005 for 0.5%).'})
        if 'quote_prefix' in attrs and not re.fullmatch(r'[A-Z0-9]+(-[A-Z0-9]+)*', attrs['quote_prefix'] or ''):
            raise serializers.ValidationError({'quote_prefix': 'Capital letters, digits and dashes only (e.g. APS-PRJ).'})
        if 'deposit_percent' in attrs and attrs['deposit_percent'] is not None and not (0 <= attrs['deposit_percent'] <= 100):
            raise serializers.ValidationError({'deposit_percent': 'Between 0 and 100.'})
        return attrs


class CatalogueItemSerializer(serializers.ModelSerializer):
    sell_excl_vat = serializers.SerializerMethodField()

    class Meta:
        model = CatalogueItem
        fields = ['id', 'category', 'code', 'tier', 'brand', 'model', 'phase', 'rated_kw', 'max_pv_kwp',
                  'usable_kwh', 'max_modules', 'unit', 'cost_excl_vat', 'sell_excl_vat', 'notes', 'sort_order']

    def get_sell_excl_vat(self, obj):
        from .engine import sell_price
        if 'margin' not in self.context:
            s = EstimateSettings.load()
            self.context.update(margin=s.target_margin, rounding=s.price_rounding)
        if obj.cost_excl_vat is None:
            return None
        return round(sell_price(obj.cost_excl_vat, self.context['margin'], self.context.get('rounding', 'item')), 2)


class EstimateListSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = SolarEstimate
        fields = ['id', 'number', 'status', 'client_name', 'site_address', 'total_incl_vat', 'created_by_name',
                  'created_at', 'updated_at', 'issued_at']

    def get_created_by_name(self, obj):
        u = obj.created_by
        return (u.get_full_name() or u.get_username()) if u else ''
