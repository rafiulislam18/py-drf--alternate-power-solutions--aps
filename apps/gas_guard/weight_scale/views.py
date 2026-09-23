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


def _subscribed_device_ids(user):
    """
    Ids of the sites this user actually pays to monitor.

    Billing is per-site, so a subscription on one site must not unlock the paid
    data on another. Staff see everything.
    """
    from apps.gas_guard.subscription.models import Subscription
    return set(
        Subscription.objects
        .filter(user=user, is_active=True, device__isnull=False)
        .values_list('device_id', flat=True)
    )


def _include_estimate(user, device, subscribed_ids=None):
    """
    Remaining-days estimates are a per-site subscriber feature.

    Pass ``subscribed_ids`` when checking several sites at once to avoid a query
    per site.
    """
    if user.is_staff:
        return True
    ids = _subscribed_device_ids(user) if subscribed_ids is None else subscribed_ids
    return device.id in ids


class SiteListView(APIView):
    """GET the caller's monitored sites, shaped for the dashboard."""

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def get(self, request):
        subscribed_ids = _subscribed_device_ids(request.user)
        sites = [
            site_payload(
                device,
                include_estimate=_include_estimate(
                    request.user, device, subscribed_ids
                ),
            )
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
            site_payload(
                device, include_estimate=_include_estimate(request.user, device)
            )
        )


class SiteConsumptionView(APIView):
    """GET a site's per-day gas consumption (subscriber feature).

    ``?days=<n>`` controls the window (default 30, capped at 90).
    """

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            device = _owned_sites(request.user).get(pk=pk)
        except ScaleDevice.DoesNotExist:
            raise Http404('Site not found.')
        # Gated on THIS site's subscription, not the account's.
        if not _include_estimate(request.user, device):
            return Response(
                {'detail': 'Consumption history requires a subscription for this site.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(daily_consumption(device, days=_window_days(request)))


class FleetConsumptionView(APIView):
    """GET per-day consumption summed across all the caller's sites.

    Backs the dashboard's fleet-wide chart (subscriber feature).
    """

    authentication_classes = [GasGuardJWTAuthentication]

    permission_classes = [IsAuthenticated]

    def get(self, request):
        sites = _owned_sites(request.user)
        if not request.user.is_staff:
            # Only the sites they pay for contribute to the fleet chart.
            subscribed_ids = _subscribed_device_ids(request.user)
            if not subscribed_ids:
                return Response(
                    {'detail': 'Consumption history requires a subscription.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            sites = sites.filter(id__in=subscribed_ids)
        return Response(
            fleet_daily_consumption(sites, days=_window_days(request))
        )
