from rest_framework import serializers

from .models import FaultAlert


class FaultAlertSerializer(serializers.ModelSerializer):
    """Read-only view of a fault alert for the pickup page."""
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)

    class Meta:
        model = FaultAlert
        fields = (
            'uuid', 'kind', 'kind_display', 'summary', 'triggered_at',
            'is_picked_up', 'picked_up_by', 'picked_up_at',
        )
        read_only_fields = fields


class FaultAlertPickupSerializer(serializers.Serializer):
    """Validates the pickup submission — just the responder's name."""
    name = serializers.CharField(max_length=120, trim_whitespace=True)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Please enter your name.")
        return value
