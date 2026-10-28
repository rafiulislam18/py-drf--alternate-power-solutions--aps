from django.contrib import admin

from apps.records.admin import TrashableAdmin

from .models import Widget


@admin.register(Widget)
class WidgetAdmin(TrashableAdmin):
    list_display = ['name', 'status']
    fields = ['name', 'status', 'amount', 'trash_status']
