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
        fields = [
            'weight',
            'unit',
            'raw_value',
            'battery_voltage',
            'rssi_dbm',
            'captured_at',
        ]


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
            'rssi_dbm',
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
            'owner',
            'tare_kg',
            'full_gas_kg',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class AdminScaleDeviceSerializer(serializers.ModelSerializer):
    """
    Staff-facing device representation — includes the API key.

    The owner-facing ``ScaleDeviceSerializer`` deliberately hides ``api_key``;
    this one exposes it because registering a device means handing that key to
    whoever flashes the hardware. Only ever served from staff-only endpoints.
    """

    owner_email = serializers.CharField(source='owner.email', read_only=True, default=None)
    reading_count = serializers.SerializerMethodField()
    last_reading_at = serializers.SerializerMethodField()

    class Meta:
        model = ScaleDevice
        fields = [
            'id',
            'device_id',
            'name',
            'location',
            'owner',
            'owner_email',
            'tare_kg',
            'full_gas_kg',
            'api_key',
            'is_active',
            'created_at',
            'reading_count',
            'last_reading_at',
        ]
        # api_key is generated server-side and rotated via its own endpoint —
        # never set or changed by a normal write.
        read_only_fields = ['id', 'created_at', 'api_key']

    def get_reading_count(self, obj):
        return obj.readings.count()

    def get_last_reading_at(self, obj):
        latest = obj.readings.first()  # model ordering: newest first
        return latest.received_at.isoformat() if latest else None
