from urllib.parse import urlparse

from django.conf import settings
from rest_framework import serializers

from .models import PushSubscription

_BASE64URL = r'^[A-Za-z0-9_-]+={0,2}$'


def allowed_endpoint(url):
    """Only real browser push services: the server POSTs to this URL, so it
    must never point anywhere else (e.g. inside our own network)."""
    parts = urlparse(url)
    host = (parts.hostname or '').lower()
    return parts.scheme == 'https' and any(
        host == suffix.lstrip('.') or host.endswith(suffix) for suffix in settings.PUSH_ALLOWED_HOST_SUFFIXES
    )


class KeysSerializer(serializers.Serializer):
    p256dh = serializers.RegexField(_BASE64URL, min_length=80, max_length=200)
    auth = serializers.RegexField(_BASE64URL, min_length=16, max_length=100)


class SubscribeSerializer(serializers.Serializer):
    """The browser's ``PushSubscription.toJSON()`` (``expirationTime`` is ignored)."""

    endpoint = serializers.URLField(max_length=1024)
    keys = KeysSerializer()

    def validate_endpoint(self, value):
        if not allowed_endpoint(value):
            raise serializers.ValidationError("This browser's push service isn't supported.")
        return value


class EndpointSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=1024)


class OptionalEndpointSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=1024, required=False)


class DeviceSerializer(serializers.ModelSerializer):
    endpoint_hash = serializers.ReadOnlyField()

    class Meta:
        model = PushSubscription
        fields = ['id', 'device_label', 'created_at', 'last_used_at', 'endpoint_hash']
