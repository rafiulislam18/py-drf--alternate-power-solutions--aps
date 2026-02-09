from rest_framework import serializers
from .models import ServiceRequest


class ServiceRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceRequest
        fields = [
            "id",
            "company_name",
            "modular_size",
            "domeshelter_size",
            "rent_or_buy",
            "rent_furniture",
            "flatpack",
            "refrigeration_type",
            "transport_or_export_address",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
