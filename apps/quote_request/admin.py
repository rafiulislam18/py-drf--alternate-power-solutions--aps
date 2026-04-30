from django.contrib import admin
from .models import QuoteRequest, SolarQuoteDetails


class SolarQuoteDetailsInline(admin.StackedInline):
    model = SolarQuoteDetails
    can_delete = False
    extra = 0


@admin.register(QuoteRequest)
class QuoteRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'phone', 'email', 'company', 'service', 'created_at', 'sent_quote')
    list_filter = ('created_at', 'sent_quote', 'service')
    search_fields = ('name', 'email', 'phone', 'company', 'message', 'service__title')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)
    list_per_page = 10
    inlines = [SolarQuoteDetailsInline]
