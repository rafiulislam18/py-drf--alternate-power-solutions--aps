"""
Admin for client tickets.

Clients raise tickets from the dashboard; the team sets a status or technician
here until the dedicated staff side is built. Client accounts and their sites
are managed as dashboard accounts (core.ClientProfile) and solar_dashboard.Site.
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import Ticket, TicketAttachment


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0
    fields = ['link', 'content_type', 'size', 'created_at']
    readonly_fields = ['link', 'content_type', 'size', 'created_at']

    @admin.display(description='File')
    def link(self, obj):
        if not obj.file:
            return '—'
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', obj.file.url, obj.original_name)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ['reference', 'title', 'client_name', 'site', 'service', 'urgency', 'status', 'technician_name',
                    'updated_at']
    list_filter = ['status', 'urgency', 'service']
    list_editable = ['status', 'technician_name']
    search_fields = ['title', 'description', 'client__username', 'client__email',
                     'client__client_profile__company_name', 'site__name']
    list_select_related = ['client__client_profile', 'site']
    autocomplete_fields = ['client', 'site']
    readonly_fields = ['reference', 'created_at', 'updated_at']
    inlines = [TicketAttachmentInline]
    fieldsets = [
        (None, {'fields': ['reference', 'status', 'technician_name']}),
        ('Request', {'fields': [
            'client', 'site', 'service', 'urgency', 'title', 'description',
            'preferred_visit_date', 'site_contact_name', 'site_contact_phone',
        ]}),
        ('Timestamps', {'fields': ['created_at', 'updated_at']}),
    ]

    @admin.display(description='Ticket', ordering='id')
    def reference(self, obj):
        return obj.reference

    @admin.display(description='Client', ordering='client__client_profile__company_name')
    def client_name(self, obj):
        try:
            return obj.client.client_profile.company_name or obj.client.username
        except Exception:  # noqa: BLE001
            return obj.client.username
