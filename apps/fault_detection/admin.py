from django.contrib import admin

from .models import AlertState, FaultAlert


@admin.register(AlertState)
class AlertStateAdmin(admin.ModelAdmin):
    list_display = ('key', 'is_active', 'last_triggered_at', 'last_recovered_at', 'updated_at')
    list_filter = ('is_active',)
    readonly_fields = ('updated_at',)
    search_fields = ('key',)


@admin.register(FaultAlert)
class FaultAlertAdmin(admin.ModelAdmin):
    list_display = ('kind', 'triggered_at', 'is_picked_up', 'picked_up_by', 'picked_up_at')
    list_filter = ('kind', ('picked_up_at', admin.EmptyFieldListFilter))
    readonly_fields = ('uuid', 'kind', 'summary', 'triggered_at', 'picked_up_by', 'picked_up_at')
    search_fields = ('picked_up_by', 'summary', 'uuid')
    date_hierarchy = 'triggered_at'
