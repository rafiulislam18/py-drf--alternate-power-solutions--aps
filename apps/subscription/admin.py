from django.contrib import admin
from .models import Client, Subscription


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'email', 'phone', 'address', 'created_at', 'updated_at')
    search_fields = ('name', 'email', 'phone', 'address')
    list_filter = ('created_at', 'updated_at')
    ordering = ('-created_at',)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'client', 'stripe_customer_id', 'stripe_subscription_id', 'subscription_length', 'call_out_balance', 'is_active', 'created_at', 'updated_at')
    search_fields = ('client__name', 'client__email', 'stripe_customer_id', 'stripe_subscription_id')
    list_filter = ('is_active', 'created_at', 'updated_at')
    ordering = ('-created_at',)
