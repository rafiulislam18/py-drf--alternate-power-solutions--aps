from django.contrib import admin

from .models import PushSubscription


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ['app', 'user', 'gg_user', 'device_label', 'created_at', 'last_used_at']
    list_filter = ['app']
    search_fields = ['user__username', 'user__email', 'gg_user__email', 'device_label']
    readonly_fields = ['app', 'user', 'gg_user', 'endpoint', 'p256dh', 'auth', 'device_label', 'created_at',
                       'last_used_at']

    def has_add_permission(self, request):
        return False  # only a browser can make one
