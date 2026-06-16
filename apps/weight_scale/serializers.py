from rest_framework import serializers

from .models import ScaleDevice, WeightReading


class WeightReadingIngestSerializer(serializers.ModelSerializer):
    """
    Validates an incoming reading from a device.

    ``device`` is intentionally omitted — it is taken from the authenticated
    request in the view, so a device can never write a reading against another
    device's id.
    """

    class Meta:
        model = WeightReading
        fields = ['weight', 'unit', 'raw_value', 'battery_voltage', 'captured_at']


class WeightReadingSerializer(serializers.ModelSerializer):
    """Read-only representation for dashboards / staff consumption."""

    device_id = serializers.CharField(source='device.device_id', read_only=True)

    class Meta:
        model = WeightReading
        fields = [
            'id',
            'device_id',
            'weight',
            'unit',
            'raw_value',
            'battery_voltage',
            'captured_at',
            'received_at',
        ]
        read_only_fields = fields


class ScaleDeviceSerializer(serializers.ModelSerializer):
    # NB: api_key is deliberately never exposed through the API.
    class Meta:
        model = ScaleDevice
        fields = [
            'id',
            'device_id',
            'name',
            'location',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']
