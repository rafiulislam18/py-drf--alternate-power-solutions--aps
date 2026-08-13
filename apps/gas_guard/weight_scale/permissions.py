from rest_framework import permissions

from .models import ScaleDevice


class IsAuthenticatedDevice(permissions.BasePermission):
    """Allow access only to requests authenticated as an active ScaleDevice."""

    message = 'A valid device API key is required.'

    def has_permission(self, request, view):
        return isinstance(request.auth, ScaleDevice)
