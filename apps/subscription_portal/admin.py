from django.contrib import admin

from .models import PortalOTP


@admin.register(PortalOTP)
class PortalOTPAdmin(admin.ModelAdmin):
    list_display = ['email', 'consumed', 'attempts', 'expires_at', 'created_at']
    list_filter = ['consumed']
    search_fields = ['email']
    readonly_fields = ['email', 'code', 'expires_at', 'attempts', 'consumed', 'created_at']
