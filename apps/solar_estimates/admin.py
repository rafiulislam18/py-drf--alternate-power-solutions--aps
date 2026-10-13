from django.contrib import admin

from .models import CatalogueItem, EstimateSettings, SolarEstimate


@admin.register(SolarEstimate)
class SolarEstimateAdmin(admin.ModelAdmin):
    list_display = ['id', 'number', 'status', 'client_name', 'site_address', 'total_incl_vat', 'updated_at']
    list_filter = ['status']
    search_fields = ['number', 'client_name', 'site_address']
    readonly_fields = ['created_at', 'updated_at', 'issued_at']


@admin.register(CatalogueItem)
class CatalogueItemAdmin(admin.ModelAdmin):
    list_display = ['model', 'category', 'tier', 'phase', 'rated_kw', 'max_pv_kwp', 'usable_kwh', 'cost_excl_vat']
    list_filter = ['category', 'tier', 'phase']


admin.site.register(EstimateSettings)
