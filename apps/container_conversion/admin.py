from django.contrib import admin
from .models import ServiceRequest


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = (
        "get_full_name",
        "email",
        "phone",
        "company_name",
        "unit_type",
        "intended_use",
        "modular_size",
        "project_timeframe",
        "budget_range",
        "transport_or_export_address",
        "is_processed",
        "created_at",
    )

    list_per_page = 10

    list_filter = (
        "unit_type",
        "intended_use",
        "modular_size",
        "preferred_contact_method",
        "project_timeframe",
        "budget_range",
        "finish_level",
        "ablution_unit",
        "electrical_installation",
        "plumbing_installation",
        "insulation",
        "interior_finishes",
        "air_conditioning",
        "solar_backup_power",
        "custom_painting_branding",
        "delivery_and_installation",
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
        ("Contact Information", {
            "fields": (
                "first_name",
                "last_name",
                "email",
                "phone",
                "preferred_contact_method",
                "company_name",
            )
        }),
        ("Unit Configuration", {
            "fields": (
                "unit_type",
                "intended_use",
                "modular_size",
            )
        }),
        ("Optional Extras", {
            "fields": (
                "ablution_unit",
                "electrical_installation",
                "plumbing_installation",
                "insulation",
                "interior_finishes",
                "air_conditioning",
                "solar_backup_power",
                "custom_painting_branding",
                "delivery_and_installation",
            )
        }),
        ("Project Details", {
            "fields": (
                "project_timeframe",
                "budget_range",
                "finish_level",
            )
        }),
        ("Delivery & Other Details", {
            "fields": (
                "transport_or_export_address",
                "additional_details",
            )
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
