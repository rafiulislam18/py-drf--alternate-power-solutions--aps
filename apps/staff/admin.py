from django.contrib import admin

from .models import StaffAccess


@admin.register(StaffAccess)
class StaffAccessAdmin(admin.ModelAdmin):
    list_display = ['user', 'updated_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['updated_at']
