"""
Public API for the fault-alert pickup page.

The email/Telegram alert links to the React page at
FRONTEND_BASE_URL/fault-detection/pickup/<uuid>/. That page:
  - GETs this endpoint to show the fault and whether it's already claimed, and
  - POSTs a name to claim it.

Claiming is one-shot: the first responder to submit locks the alert (a DB-level
guard prevents a race from double-claiming), and a "picked up by X" confirmation
is sent on the same channels as the original fault. Endpoints are public
(AllowAny) so an on-call responder can act straight from the email link without
logging in — matching the shared-link pattern used by the solar dashboard.
"""

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .alerts import dispatch_pickup_confirmation
from .models import FaultAlert
from .serializers import FaultAlertPickupSerializer, FaultAlertSerializer

logger = logging.getLogger(__name__)


class FaultAlertPickupView(APIView):
    """GET the alert's pickup status; POST a name to claim it (once)."""
    permission_classes = [AllowAny]

    def get_object(self, uuid):
        try:
            return FaultAlert.objects.get(uuid=uuid)
        except FaultAlert.DoesNotExist:
            return None

    def get(self, request, uuid):
        alert = self.get_object(uuid)
        if alert is None:
            return Response({'error': 'Alert not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(FaultAlertSerializer(alert).data, status=status.HTTP_200_OK)

    def post(self, request, uuid):
        alert = self.get_object(uuid)
        if alert is None:
            return Response({'error': 'Alert not found'}, status=status.HTTP_404_NOT_FOUND)

        serializer = FaultAlertPickupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data['name']

        # One-shot claim, race-safe: only the row that is still unclaimed
        # (picked_up_by='') is updated. If another responder claimed it a moment
        # ago, the conditional UPDATE matches 0 rows and we report it as taken.
        now = timezone.now()
        with transaction.atomic():
            claimed = (
                FaultAlert.objects
                .filter(pk=alert.pk, picked_up_by='')
                .update(picked_up_by=name, picked_up_at=now)
            )

        alert.refresh_from_db()

        if not claimed:
            # Someone got here first — return 409 with who has it.
            return Response(
                {
                    'error': 'already_picked_up',
                    'detail': f"This alert was already picked up by {alert.picked_up_by}.",
                    'alert': FaultAlertSerializer(alert).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        # We won the claim — send the "picked up by X" confirmation. Delivery
        # failures are swallowed inside dispatch so they never fail the response.
        try:
            dispatch_pickup_confirmation(alert)
        except Exception as exc:  # defensive: confirmation must not break the claim
            logger.error("Pickup confirmation dispatch failed for %s: %s", alert.uuid, exc)

        return Response(FaultAlertSerializer(alert).data, status=status.HTTP_200_OK)
