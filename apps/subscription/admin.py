from django.contrib import admin
from .models import Client, Payment, Subscription


class SubscriptionInline(admin.TabularInline):  # or admin.StackedInline for full form
    model = Subscription
    extra = 0  # how many empty forms to show for adding new subscriptions
    fields = ('inverter_type', 'address', 'payfast_token', 'payfast_payment_id', 'subscription_length', 'call_out_balance', 'is_active')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'email', 'phone', 'created_at', 'updated_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    search_fields = ('name', 'email', 'phone')
    list_filter = ('created_at', 'updated_at')
    ordering = ('-created_at',)
    list_per_page = 10

    inlines = [SubscriptionInline]  # Show subscriptions under client

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
    list_display = ('id', 'client', 'inverter_type', 'address', 'payfast_token', 'payfast_payment_id', 'subscription_length', 'call_out_balance', 'is_active', 'last_payment_date', 'created_at', 'updated_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    search_fields = ('client__name', 'client__email', 'inverter_type', 'address', 'payfast_token', 'payfast_payment_id')
    list_filter = ('is_active', 'created_at', 'updated_at')
    ordering = ('-created_at',)
    list_per_page = 10

    fieldsets = (
        ('Subscription Details', {
            'fields': (
                'id', 'client', 'inverter_type', 'address',
                'subscription_length', 'call_out_balance', 'is_active'
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
    """Read-only view of the immutable PayFast payment audit trail."""

    list_display = ('id', 'client', 'subscription', 'amount_gross', 'payment_status', 'item_name', 'pf_payment_id', 'created_at')
    search_fields = ('client__name', 'client__email', 'pf_payment_id', 'm_payment_id', 'item_name')
    list_filter = ('payment_status', 'created_at')
    ordering = ('-created_at',)
    list_per_page = 20

    # The audit trail is written only by the PayFast ITN handler.
    readonly_fields = (
        'id', 'client', 'subscription', 'amount_gross', 'amount_fee', 'amount_net',
        'pf_payment_id', 'm_payment_id', 'payfast_token', 'payment_status',
        'item_name', 'raw_payload', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
