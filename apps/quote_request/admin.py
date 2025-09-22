from django.contrib import admin
from .models import QuoteRequest


@admin.register(QuoteRequest)
class QuoteRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'phone', 'email', 'service', 'created_at', 'sent_quote')
    list_filter = ('created_at', 'sent_quote', 'service')
    search_fields = ('name', 'email', 'phone', 'message', 'service__title')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)