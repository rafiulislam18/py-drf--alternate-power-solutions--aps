"""
Staff-only endpoints backing the Gas Guard admin panel (/gas-guard/admin).

These let the APS team register a scale, read back its API key to flash onto the
hardware, assign the site to a client, and check that readings are arriving —
without going through Django admin.

Every view here is gated on ``IsAdminUser`` against a *Gas Guard* staff user
(``gg_users.GasGuardUser.is_staff``). Gas Guard has its own user table and JWT
audience, entirely separate from the APS website's ``auth.User``, so an APS
admin is NOT an admin here and vice versa.

Note these serve ``AdminScaleDeviceSerializer``, which includes ``api_key`` —
that field must never leak into an owner-facing endpoint.
"""

import logging

from django.db.models import Count, Max
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.gas_guard.users.authentication import GasGuardJWTAuthentication
from apps.gas_guard.users.models import GasGuardUser
from .models import ScaleDevice, generate_api_key
from .serializers import AdminScaleDeviceSerializer, WeightReadingSerializer

logger = logging.getLogger(__name__)


class AdminSiteListView(APIView):
    """
    GET  — every registered site, with its API key, owner and reading stats.
    POST — register a new site; the response carries its generated API key.
    """

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAdminUser]

    def get(self, request):
        devices = (
            ScaleDevice.objects.select_related('owner')
            .annotate(_n=Count('readings'), _last=Max('readings__received_at'))
            .order_by('-created_at')
        )
        serializer = AdminScaleDeviceSerializer(devices, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = AdminScaleDeviceSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': 'Invalid input.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        device = serializer.save()
        logger.info(
            f'Site registered by {request.user.email}: '
            f'{device.name or device.device_id} (id={device.id})'
        )
        return Response(
            AdminScaleDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED,
        )


class AdminSiteDetailView(APIView):
    """
    GET    — one site, with its API key and stats.
    PATCH  — edit its details, including reassigning ``owner`` to a client.
    DELETE — remove the site (and, by cascade, its readings and subscription).
    """

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAdminUser]

    def _get(self, pk):
        try:
            return ScaleDevice.objects.select_related('owner').get(pk=pk)
        except ScaleDevice.DoesNotExist:
            raise Http404('Site not found.')

    def get(self, request, pk):
        return Response(AdminScaleDeviceSerializer(self._get(pk)).data)

    def patch(self, request, pk):
        device = self._get(pk)
        serializer = AdminScaleDeviceSerializer(device, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'detail': 'Invalid input.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        logger.info(f'Site {pk} updated by {request.user.email}')
        return Response(AdminScaleDeviceSerializer(device).data)

    def delete(self, request, pk):
        device = self._get(pk)
        label = device.name or device.device_id
        device.delete()
        logger.warning(f'Site {pk} ({label}) deleted by {request.user.email}')
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminSiteRegenerateKeyView(APIView):
    """
    POST — issue a fresh API key for a site.

    The previous key stops working immediately, so the device must be reflashed
    with the new one. Used when a key leaks or is lost.
    """

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            device = ScaleDevice.objects.get(pk=pk)
        except ScaleDevice.DoesNotExist:
            raise Http404('Site not found.')

        device.api_key = generate_api_key()
        device.save(update_fields=['api_key', 'updated_at'])
        logger.warning(
            f'API key regenerated for site {pk} by {request.user.email} — '
            f'the device must be reflashed.'
        )
        return Response(AdminScaleDeviceSerializer(device).data)


class AdminSiteReadingsView(APIView):
    """
    GET — the most recent readings for one site.

    Lets an admin confirm a freshly flashed device is actually posting.
    ``?limit=<n>`` caps how many come back (default 25, max 200).
    """

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        try:
            device = ScaleDevice.objects.get(pk=pk)
        except ScaleDevice.DoesNotExist:
            raise Http404('Site not found.')

        try:
            limit = min(max(int(request.query_params.get('limit', 25)), 1), 200)
        except ValueError:
            limit = 25

        readings = device.readings.all()[:limit]  # model ordering: newest first
        return Response(WeightReadingSerializer(readings, many=True).data)


class AdminClientListView(APIView):
    """
    GET — Gas Guard accounts a site can be assigned to.

    Feeds the owner picker in the admin panel. Returns the fields needed to
    identify a person plus their site count, so staff can tell two similar
    accounts apart. ``?q=`` filters on email or name.
    """

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAdminUser]

    def get(self, request):
        clients = GasGuardUser.objects.filter(is_active=True)
        query = (request.query_params.get('q') or '').strip()
        if query:
            from django.db.models import Q

            clients = clients.filter(
                Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
            )
        clients = clients.annotate(site_count=Count('devices')).order_by('email')

        return Response([
            {
                'id': c.id,
                'email': c.email,
                'firstName': c.first_name,
                'lastName': c.last_name,
                'tier': c.tier,
                'isStaff': c.is_staff,
                'siteCount': c.site_count,
            }
            for c in clients
        ])


class AdminSiteSubscriptionsView(APIView):
    """
    GET — every site's billing state, keyed by device id.

    Billing is per-site, so this mirrors what the client sees on their Plans
    page and lets staff answer billing questions without the Django admin.
    """

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.gas_guard.subscription.models import Subscription

        subs = Subscription.objects.filter(device__isnull=False).select_related(
            'device', 'user'
        )
        return Response({
            str(s.device_id): {
                'monitoringActive': s.is_active,
                'monitoringMonths': s.subscription_length,
                'lastPaymentDate': (
                    s.last_payment_date.isoformat() if s.last_payment_date else None
                ),
                'swapAddonActive': s.swap_addon_active,
                'swapAddonMonths': s.swap_addon_length,
            }
            for s in subs
        })
