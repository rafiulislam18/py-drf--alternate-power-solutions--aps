from django.contrib import admin

from .models import DemoBooking


@admin.register(DemoBooking)
class DemoBookingAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'name', 'email', 'sites', 'handled', 'created_at']
    list_filter = ['handled', 'created_at']
    list_editable = ['handled']
    search_fields = ['name', 'email', 'company', 'message']
    date_hierarchy = 'created_at'
    readonly_fields = ['name', 'email', 'company', 'sites', 'message', 'created_at']
