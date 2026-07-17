from django.contrib import admin

from .models import ExportedSubscription


@admin.register(ExportedSubscription)
class ExportedSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('key', 'app_label', 'subscription_id', 'first_exported_at', 'last_synced_at')
    list_filter = ('app_label',)
    search_fields = ('subscription_id',)
    readonly_fields = ('app_label', 'subscription_id', 'first_exported_at', 'last_synced_at')
