import logging

from django.http import Http404
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.gas_guard.users.authentication import GasGuardJWTAuthentication
from .authentication import DeviceAPIKeyAuthentication
from .models import ScaleDevice, WeightReading
from .permissions import IsAuthenticatedDevice
from .serializers import (
    ScaleDeviceSerializer,
    WeightReadingIngestSerializer,
    WeightReadingSerializer,
)
from .services import daily_consumption, fleet_daily_consumption, site_payload


def _window_days(request, default=30, cap=90):
    """Parse the ``?days=<n>`` query param, clamped to a sane window."""
    try:
        return min(max(int(request.query_params.get('days', default)), 1), cap)
    except ValueError:
        return default

logger = logging.getLogger(__name__)


class ReadingPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500


class WeightReadingIngestView(APIView):
    """
    POST endpoint that devices call to submit a weight reading.

    Auth: ``X-API-Key`` header identifying the ScaleDevice. The device is
    resolved from the key, so the request body never carries a device id.

    Note: this view sets its own ``authentication_classes`` so it does not use
    the project-wide JWT default. Validation is handled manually and returns
    ``serializer.errors`` so it stays compatible with the project's custom
    exception handler (which expects a ``detail`` key for handled exceptions).
    """

    authentication_classes = [DeviceAPIKeyAuthentication]
    permission_classes = [IsAuthenticatedDevice]

    def post(self, request):
        device = request.auth
        serializer = WeightReadingIngestSerializer(data=request.data)
        if serializer.is_valid():
            reading = serializer.save(device=device)
            logger.info(
                f"Weight reading ingested: device={device.device_id} "
                f"reading_id={reading.id}"
            )
            return Response(
                WeightReadingSerializer(reading).data,
                status=status.HTTP_201_CREATED,
            )
        logger.warning(
            f"Weight reading ingest rejected: device={device.device_id} "
            f"errors={serializer.errors}"
        )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class WeightReadingListView(APIView):
    """
    GET a paginated list of readings for staff / dashboards.

    Filters: ``?device_id=<id>`` and ``?since=<ISO 8601 datetime>``.
    """

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAdminUser]
    pagination_class = ReadingPagination

    def get(self, request):
        qs = WeightReading.objects.select_related('device')

        device_id = request.query_params.get('device_id')
        if device_id:
            qs = qs.filter(device__device_id=device_id)

        since = request.query_params.get('since')
        if since:
            qs = qs.filter(received_at__gte=since)

        logger.debug(
            f"Weight reading list queried: device_id={device_id} since={since}"
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = WeightReadingSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ScaleDeviceListView(APIView):
    """GET a list of registered devices (staff only)."""

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAdminUser]

    def get(self, request):
        logger.debug("Scale device list queried")
        serializer = ScaleDeviceSerializer(ScaleDevice.objects.all(), many=True)
        return Response(serializer.data)


def _owned_sites(user):
    """Active devices visible to this user — staff see the whole fleet."""
    qs = ScaleDevice.objects.filter(is_active=True)
    if not user.is_staff:
        qs = qs.filter(owner=user)
    return qs


def _include_estimate(user):
    """Remaining-days estimates are a subscriber feature."""
    return user.is_staff or user.tier == user.Tier.SUBSCRIBED


class SiteListView(APIView):
    """GET the caller's monitored sites, shaped for the dashboard."""

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def get(self, request):
        include_estimate = _include_estimate(request.user)
        sites = [
            site_payload(device, include_estimate=include_estimate)
            for device in _owned_sites(request.user)
        ]
        logger.debug(f"Site list queried: user={request.user} count={len(sites)}")
        return Response(sites)


class SiteDetailView(APIView):
    """GET a single monitored site owned by the caller."""

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            device = _owned_sites(request.user).get(pk=pk)
        except ScaleDevice.DoesNotExist:
            raise Http404('Site not found.')
        return Response(
            site_payload(device, include_estimate=_include_estimate(request.user))
        )


class SiteConsumptionView(APIView):
    """GET a site's per-day gas consumption (subscriber feature).

    ``?days=<n>`` controls the window (default 30, capped at 90).
    """

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not _include_estimate(request.user):
            return Response(
                {'detail': 'Consumption history requires a subscription.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            device = _owned_sites(request.user).get(pk=pk)
        except ScaleDevice.DoesNotExist:
            raise Http404('Site not found.')
        return Response(daily_consumption(device, days=_window_days(request)))


class FleetConsumptionView(APIView):
    """GET per-day consumption summed across all the caller's sites.

    Backs the dashboard's fleet-wide chart (subscriber feature).
    """

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _include_estimate(request.user):
            return Response(
                {'detail': 'Consumption history requires a subscription.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(
            fleet_daily_consumption(
                _owned_sites(request.user), days=_window_days(request)
            )
        )
