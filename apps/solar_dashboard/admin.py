from django.contrib import admin
from .models import SolarReport, SiteData


class SiteDataInline(admin.TabularInline):
    model = SiteData
    extra = 0
    fields = [
        'order', 'site_name', 'has_battery',
        'solar_yield', 'battery_charge', 'usable_solar',
        'estimated_saving', 'used_from_battery',
        'sell_to_grid_kwh', 'sell_to_grid_r',
        'grid_consumption', 'total_consumption',
    ]


@admin.register(SolarReport)
class SolarReportAdmin(admin.ModelAdmin):
    list_display = ['get_client_name', 'period_start', 'period_end', 'report_date', 'created_at', 'uuid']
    list_filter = ['report_date']
    search_fields = ['client__username', 'client__client_profile__company_name']
    readonly_fields = ['uuid', 'created_at', 'updated_at']
    inlines = [SiteDataInline]
    autocomplete_fields = ['client']

    @admin.display(description='Client')
    def get_client_name(self, obj):
        if not obj.client:
            return '(no client)'
        try:
            return obj.client.client_profile.company_name or obj.client.username
        except Exception:
            return obj.client.username
