from django.contrib import admin
from .models import Client, Subscription


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