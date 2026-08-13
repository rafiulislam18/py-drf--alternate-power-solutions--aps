from django.contrib import admin

from .models import Payment, Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'is_active', 'subscription_length',
                    'swap_addon_active', 'swap_addon_length',
                    'last_payment_date', 'created_at']
    list_filter = ['is_active', 'swap_addon_active']
    search_fields = ['user__email', 'user__first_name', 'user__last_name',
                     'payfast_payment_id', 'swap_addon_payment_id']
    readonly_fields = ['payfast_token', 'payfast_payment_id',
                       'swap_addon_token', 'swap_addon_payment_id',
                       'created_at', 'updated_at', 'last_payment_date',
                       'swap_addon_last_payment_date']


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Immutable audit trail of confirmed PayFast payments — read-only."""

    list_display = ['created_at', 'user', 'plan', 'amount_gross',
                    'payment_status', 'pf_payment_id']
    list_filter = ['plan', 'payment_status', 'created_at']
    search_fields = ['user__email', 'pf_payment_id', 'm_payment_id',
                     'item_name']
    date_hierarchy = 'created_at'
    # Payments are a historical record — never edited or hand-created here.
    readonly_fields = [f.name for f in Payment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
