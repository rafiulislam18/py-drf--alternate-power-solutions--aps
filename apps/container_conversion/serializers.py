from rest_framework import serializers
from .models import ServiceRequest


class ServiceRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceRequest
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "phone",
            "preferred_contact_method",
            "company_name",
            "unit_type",
            "intended_use",
            "ablution_included",
            "modular_size",
            "domeshelter_size",
            "rent_or_buy",
            "flatpack",
            "rent_furniture",
            "refrigeration_type",
            "transport_or_export_address",
            "additional_details",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
