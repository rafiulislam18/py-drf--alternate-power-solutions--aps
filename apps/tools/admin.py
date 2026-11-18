from django.contrib import admin

from apps.records.admin import TrashableAdmin

from .models import Booking, BookingItem, Movement, Tool, ToolSettings, ToolType


@admin.register(Tool)
class ToolAdmin(TrashableAdmin):
    list_display = ['tool_id', 'name', 'type', 'brand', 'model', 'serial', 'quantity', 'condition']
    list_filter = ['condition', 'type']
    search_fields = ['name', 'brand', 'model', 'serial', 'number']
    readonly_fields = ['tool_id', 'created_by', 'created_at']

    def has_add_permission(self, request):
        return False  # new tools get their number on the dashboard

    @admin.display(description='Tool ID', ordering='number')
    def tool_id(self, tool):
        return tool.tool_id


class BookingItemInline(admin.TabularInline):
    model = BookingItem
    extra = 0
    fields = ['tool_label', 'quantity', 'return_movement', 'return_condition', 'return_note']
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class MovementInline(admin.TabularInline):
    model = Movement
    extra = 0
    fields = ['kind', 'date', 'recorded_by', 'checked_by', 'checked_at', 'check_note', 'problem_flagged']
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Booking)
class BookingAdmin(TrashableAdmin):
    list_display = ['booking_id', 'technician', 'date_out', 'due_date', 'long_term_kit', 'archived_at']
    list_filter = ['long_term_kit']
    search_fields = ['number', 'technician__first_name', 'technician__username', 'items__tool_label']
    readonly_fields = ['booking_id', 'technician', 'date_out', 'created_by', 'created_at', 'overdue_alert_at']
    raw_id_fields = ['job']
    inlines = [BookingItemInline, MovementInline]

    def has_add_permission(self, request):
        return False  # bookings are made on the dashboard

    @admin.display(description='Booking', ordering='number')
    def booking_id(self, booking):
        return booking.booking_id


admin.site.register(ToolSettings)


@admin.register(ToolType)
class ToolTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'order', 'is_active']
    list_editable = ['order', 'is_active']

    def has_delete_permission(self, request, obj=None):
        return False  # tools keep pointing at their type; switch it off instead
