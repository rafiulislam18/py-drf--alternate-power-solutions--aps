from django.contrib import admin
from .models import ServiceRequest


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "modular_size",
        "rent_or_buy",
        "domeshelter_size",
        "refrigeration_type",
        "rent_furniture",
        "flatpack",
        "is_processed",
        "created_at",
    )

    list_per_page = 10

    list_filter = (
        "modular_size",
        "rent_or_buy",
        "domeshelter_size",
        "refrigeration_type",
        "is_processed",
        "created_at",
    )

    search_fields = (
        "company_name",
        "transport_or_export_address",
    )

    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
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
