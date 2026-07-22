from django.contrib import admin

from .models import ExportedQuote


@admin.register(ExportedQuote)
class ExportedQuoteAdmin(admin.ModelAdmin):
    list_display = ('key', 'kind', 'quote_id', 'first_exported_at', 'last_synced_at')
    list_filter = ('kind',)
    search_fields = ('quote_id',)
    readonly_fields = ('kind', 'quote_id', 'first_exported_at', 'last_synced_at')
