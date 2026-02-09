from django.contrib import admin
from .models import ServiceRequest


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = (
        "get_full_name",
        "email",
        "phone",
        "company_name",
        "modular_size",
        "rent_or_buy",
        "domeshelter_size",
        "refrigeration_type",
        "is_processed",
        "created_at",
    )

    list_per_page = 10

    list_filter = (
        "modular_size",
        "rent_or_buy",
        "domeshelter_size",
        "refrigeration_type",
        "preferred_contact_method",
        "is_processed",
        "created_at",
    )

    search_fields = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "company_name",
        "transport_or_export_address",
    )

    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Personal / Contact Info", {
            "fields": (
                "first_name",
                "last_name",
                "email",
                "phone",
                "preferred_contact_method",
            )
        }),
        ("Company Info", {
            "fields": ("company_name",)
        }),
        ("Container / Modular Details", {
            "fields": (
                "modular_size",
                "domeshelter_size",
                "rent_or_buy",
            )
        }),
        ("Optional Add-ons", {
            "fields": (
                "flatpack",
                "rent_furniture",
                "ablution",
                "refrigeration_type",
            )
        }),
        ("Transport / Export", {
            "fields": ("transport_or_export_address",)
        }),
        ("Admin", {
            "fields": (
                "is_processed",
                "notes",
                "created_at",
                "updated_at",
            )
        }),
    )

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"
    get_full_name.short_description = "Name"
