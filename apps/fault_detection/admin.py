from django.contrib import admin

from .models import AlertState


@admin.register(AlertState)
class AlertStateAdmin(admin.ModelAdmin):
    list_display = ('key', 'is_active', 'last_triggered_at', 'last_recovered_at', 'updated_at')
    list_filter = ('is_active',)
    readonly_fields = ('updated_at',)
    search_fields = ('key',)
