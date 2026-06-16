from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import DeviceAPIKeyAuthentication
from .models import ScaleDevice, WeightReading
from .permissions import IsAuthenticatedDevice
from .serializers import (
    ScaleDeviceSerializer,
    WeightReadingIngestSerializer,
    WeightReadingSerializer,
)


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
        serializer = WeightReadingIngestSerializer(data=request.data)
        if serializer.is_valid():
            reading = serializer.save(device=request.auth)
            return Response(
                WeightReadingSerializer(reading).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class WeightReadingListView(APIView):
    """
    GET a paginated list of readings for staff / dashboards.

    Filters: ``?device_id=<id>`` and ``?since=<ISO 8601 datetime>``.
    """

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

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = WeightReadingSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ScaleDeviceListView(APIView):
    """GET a list of registered devices (staff only)."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        serializer = ScaleDeviceSerializer(ScaleDevice.objects.all(), many=True)
        return Response(serializer.data)
