from django.contrib import admin

from .models import ScaleDevice, WeightReading


@admin.register(ScaleDevice)
class ScaleDeviceAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'device_id', 'location', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['device_id', 'name', 'location']
    # api_key is read-only so staff can copy it onto the device, but it is
    # auto-generated and never editable by hand.
    readonly_fields = ['api_key', 'created_at', 'updated_at']


@admin.register(WeightReading)
class WeightReadingAdmin(admin.ModelAdmin):
    list_display = ['device', 'weight', 'unit', 'received_at', 'captured_at']
    list_filter = ['unit', 'device']
    search_fields = ['device__device_id', 'device__name']
    date_hierarchy = 'received_at'
    readonly_fields = ['received_at']
