from django.contrib import admin

from .models import AccountPreferences


@admin.register(AccountPreferences)
class AccountPreferencesAdmin(admin.ModelAdmin):
    """Read-only: people change their own appearance on the dashboard."""

    list_display = ('user', 'updated_at')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('user', 'appearance', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
