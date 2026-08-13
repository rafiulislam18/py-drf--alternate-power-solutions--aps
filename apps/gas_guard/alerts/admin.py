from django.contrib import admin

from .models import LowGasAlertState


@admin.register(LowGasAlertState)
class LowGasAlertStateAdmin(admin.ModelAdmin):
    list_display = ['device', 'is_active', 'last_pct', 'last_triggered_at', 'last_recovered_at']
    list_filter = ['is_active']
    search_fields = ['device__name', 'device__device_id']
    readonly_fields = ['device', 'is_active', 'last_pct', 'last_triggered_at',
                       'last_recovered_at', 'updated_at']
