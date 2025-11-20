from django.contrib import admin
from .models import Client, Request

class RequestInline(admin.TabularInline):  # or admin.StackedInline for full form
    model = Request
    extra = 0
    fields = ('address', 'paid', 'payfast_token', 'payfast_payment_id', 'last_payment_date')
    readonly_fields = ('id', 'created_at', 'updated_at')

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'email', 'phone', 'created_at', 'updated_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    search_fields = ('name', 'email', 'phone')
    list_filter = ('created_at', 'updated_at')
    ordering = ('-created_at',)
    list_per_page = 10

    inlines = [RequestInline]
    fieldsets = (
        ('Client Information', {
            'fields': ('id', 'name', 'email', 'phone', 'note'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'client', 'address', 'paid', 'payfast_token', 'payfast_payment_id', 'last_payment_date', 'created_at', 'updated_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    search_fields = ('client__name', 'client__email', 'address', 'payfast_token', 'payfast_payment_id')
    list_filter = ('paid', 'created_at', 'updated_at')
    ordering = ('-created_at',)
    list_per_page = 10
    
    fieldsets = (
        ('Request Details', {
            'fields': (
                'id', 'client', 'address', 'paid'
            ),
        }),
        ('PayFast Info', {
            'fields': ('payfast_token', 'payfast_payment_id', 'last_payment_date'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
        }),
    )
