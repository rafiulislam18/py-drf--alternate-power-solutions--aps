import logging

from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions

from .models import ScaleDevice

logger = logging.getLogger(__name__)

# Django turns the "X-API-Key" request header into this WSGI META key.
API_KEY_HEADER = 'HTTP_X_API_KEY'


class DeviceAPIKeyAuthentication(authentication.BaseAuthentication):
    """
    Authenticate a scale device by an API key sent in the ``X-API-Key`` header.

    Returns ``(AnonymousUser, device)`` on success: there is no Django user
    behind a device, so ``request.user`` stays anonymous while ``request.auth``
    carries the ScaleDevice. Guard endpoints with ``IsAuthenticatedDevice``,
    which inspects ``request.auth``.
    """

    def authenticate(self, request):
        api_key = request.META.get(API_KEY_HEADER)
        if not api_key:
            return None  # No credentials supplied -> let other authenticators try.

        try:
            device = ScaleDevice.objects.get(api_key=api_key, is_active=True)
        except ScaleDevice.DoesNotExist:
            # Security-relevant: a request presented an API key we don't
            # recognise (or one for a deactivated device). Log the last 4 chars
            # only — never the full key.
            logger.warning(
                'Device API-key auth failed: no active device for key ending ...%s',
                api_key[-4:],
            )
            raise exceptions.AuthenticationFailed('Invalid or inactive device API key.')

        return (AnonymousUser(), device)

    def authenticate_header(self, request):
        # Makes DRF return 401 (not 403) when the header is missing/invalid.
        return 'X-API-Key'
