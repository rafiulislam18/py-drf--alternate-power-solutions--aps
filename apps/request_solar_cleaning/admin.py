from decimal import Decimal
from uuid import uuid4

from django.contrib import admin
from .models import Client, Payment, Subscription

class SubscriptionInline(admin.TabularInline):  # or admin.StackedInline for full form
    model = Subscription
    extra = 0
    fields = ('inverter_type', 'inverter_size', 'installed_panels_count', 'address', 'payfast_token', 'payfast_payment_id', 'is_active', 'subscription_length', 'last_payment_date')
    readonly_fields = ('id', 'created_at', 'updated_at')

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'email', 'phone', 'created_at', 'updated_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    search_fields = ('name', 'email', 'phone')
    list_filter = ('created_at', 'updated_at')
    ordering = ('-created_at',)
    list_per_page = 10

    inlines = [SubscriptionInline]
    fieldsets = (
        ('Client Information', {
            'fields': ('id', 'name', 'email', 'phone', 'note'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'client', 'inverter_type', 'inverter_size', 'installed_panels_count', 'address', 'payfast_token', 'payfast_payment_id', 'subscription_length', 'is_active', 'last_payment_date', 'created_at', 'updated_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    search_fields = ('client__name', 'client__email', 'inverter_type', 'inverter_size', 'installed_panels_count', 'address', 'payfast_token', 'payfast_payment_id')
    list_filter = ('is_active', 'created_at', 'updated_at')
    ordering = ('-created_at',)
    list_per_page = 10
    
    fieldsets = (
        ('Subscription Details', {
            'fields': (
                'id', 'client', 'inverter_type', 'inverter_size', 'installed_panels_count', 'address',
                'subscription_length', 'is_active'
            ),
        }),
        ('PayFast Info', {
            'fields': ('payfast_token', 'payfast_payment_id', 'last_payment_date'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """
    The PayFast payment audit trail (R199 Solar & Inverter Maintenance).

    Rows are normally written by the PayFast ITN handler. Manual creation is
    allowed here so staff can capture a payment taken outside the automated
    flow — an EFT, a correction, or backfilled history — and have it appear in
    the subscriber's self-service portal.

    Guard rails: rows already written by the ITN (identifiable by their stored
    payload) stay read-only, so a real PayFast record can never be edited after
    the fact. Only manually captured rows remain editable.
    """

    list_display = (
        'id', 'client', 'subscription', 'amount_gross', 'payment_status',
        'item_name', 'pf_payment_id', 'source', 'created_at',
    )
    search_fields = ('client__name', 'client__email', 'pf_payment_id', 'm_payment_id', 'item_name')
    list_filter = ('payment_status', 'created_at')
    autocomplete_fields = ('client', 'subscription')
    ordering = ('-created_at',)
    list_per_page = 20
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Who', {
            'fields': ('client', 'subscription'),
            'description': (
                'Pick the subscription this payment was taken for. The client '
                'is filled in automatically from it if left blank.'
            ),
        }),
        ('Amount', {
            'fields': ('amount_gross', 'amount_fee', 'amount_net'),
            'description': 'Gross defaults to R199.00 if left blank.',
        }),
        ('Payment details', {
            'fields': ('payment_status', 'item_name', 'pf_payment_id', 'm_payment_id', 'payfast_token'),
            'description': (
                'Leave the PayFast reference blank to have one generated for a '
                'manually captured payment.'
            ),
        }),
        ('Audit', {
            'fields': ('raw_payload', 'created_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Source')
    def source(self, obj):
        """Whether this row came from PayFast or was captured by hand."""
        return 'Manual' if self._is_manual(obj) else 'PayFast'

    @staticmethod
    def _is_manual(obj):
        payload = obj.raw_payload or {}
        return bool(payload.get('manual_entry')) or not payload

    def get_readonly_fields(self, request, obj=None):
        # created_at is auto_now_add, so it is never editable.
        base = ('created_at',)
        if obj is None:
            return base
        # An ITN-written row is a real payment record — freeze it entirely.
        if not self._is_manual(obj):
            return base + (
                'client', 'subscription', 'amount_gross', 'amount_fee', 'amount_net',
                'pf_payment_id', 'm_payment_id', 'payfast_token', 'payment_status',
                'item_name', 'raw_payload',
            )
        return base

    def has_change_permission(self, request, obj=None):
        # Real PayFast records stay immutable; manual ones can be corrected.
        if obj is not None and not self._is_manual(obj):
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        """Fill in the fields staff shouldn't have to type by hand."""
        if obj.subscription:
            if not obj.client:
                obj.client = obj.subscription.client
            if not obj.m_payment_id:
                obj.m_payment_id = str(obj.subscription_id)

        if obj.amount_gross is None:
            obj.amount_gross = Decimal('199.00')
        if obj.amount_net is None and obj.amount_fee is not None:
            obj.amount_net = obj.amount_gross - obj.amount_fee

        if not obj.payment_status:
            obj.payment_status = 'COMPLETE'
        if not obj.item_name:
            obj.item_name = 'Monthly Subscription'

        # pf_payment_id is unique and NOT NULL — mint one for manual entries.
        if not obj.pf_payment_id:
            obj.pf_payment_id = f'manual-{uuid4().hex[:16]}'

        if not change:
            payload = obj.raw_payload or {}
            payload.update({
                'manual_entry': True,
                'captured_by': request.user.get_username(),
                'note': 'Captured in Django admin, not a PayFast notification.',
            })
            obj.raw_payload = payload

        super().save_model(request, obj, form, change)
