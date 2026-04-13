from rest_framework import serializers
from .models import ServiceRequest, ContainerProject


class ContainerProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContainerProject
        fields = [
            "id",
            "title",
            "short_description",
            "long_description",
            "image",
            "image_2",
            "image_3",
            "location",
            "completion_date",
            "duration",
            "features",
            "appreciation_mark",
        ]


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
            "modular_size",
            "ablution_unit",
            "electrical_installation",
            "plumbing_installation",
            "insulation",
            "interior_finishes",
            "air_conditioning",
            "solar_backup_power",
            "custom_painting_branding",
            "delivery_and_installation",
            "project_timeframe",
            "budget_range",
            "finish_level",
            "transport_or_export_address",
            "additional_details",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
