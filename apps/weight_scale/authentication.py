from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions

from .models import ScaleDevice

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
            raise exceptions.AuthenticationFailed('Invalid or inactive device API key.')

        return (AnonymousUser(), device)

    def authenticate_header(self, request):
        # Makes DRF return 401 (not 403) when the header is missing/invalid.
        return 'X-API-Key'
