"""
Staff side of client tickets (mounted at ``/client-portal/staff/``) — the
dashboard's "Client tickets" pages. Staff only, per permission
(``apps.staff.permissions.HasDashboardPerm``); clients get 403.

- ``GET   staff/tickets/summary/`` → counts per status group (optional ``?client=``).
- ``GET   staff/tickets/``         → every client's tickets: the client list's
  filters (status_group, status, service, urgency, site, search, ordering, page)
  plus ``client=<user id>``; search also matches the client's name, username
  and email; ``ordering=client`` sorts by client name.
- ``POST  staff/tickets/``         → raise a ticket for a client: the client's
  new-ticket fields plus ``client=<user id>`` (multipart, ``files`` optional).
- ``GET   staff/tickets/<id>/``    → one ticket with its client and attachments.
- ``PATCH staff/tickets/<id>/``    → {status?, technician_name?, notify_client?}.
  A status change emails the client (to a confirmed email only) unless
  ``notify_client`` is false.
- ``DELETE staff/tickets/<id>/``   → delete the ticket with its chat and files.

Each needs the matching staff permission (``apps.staff.catalogue``, section
``tickets``): view, create, edit (status), assign (technician), delete.
"""

import logging
from functools import partial

from django.contrib.auth.models import User
from rest_framework import generics, serializers as drf_serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.staff.permissions import HasDashboardPerm
from utils.exceptions import custom_exception_handler

from .emails import send_status_update
from .realtime import ticket_changed
from django.db.models import F

from .models import Ticket, TicketMessage
from .serializers import (
    StaffTicketDetailSerializer, StaffTicketSerializer, StaffTicketUpdateSerializer, TicketCreateSerializer,
    validate_attachments,
)
from .throttling import TicketCreateThrottle
from .views import (
    TicketPagination, _small_int, filter_tickets, raise_ticket, summarise_tickets, unread_totals, with_counts,
)

logger = logging.getLogger('apps.client_portal')

_CLIENT_SEARCH = (
    'client__client_profile__company_name__icontains',
    'client__username__icontains',
    'client__email__icontains',
)
_CLIENT_ORDERING = {'client': 'client__client_profile__company_name', 'urgency': 'urgency'}


class _StaffView:
    permission_classes = [HasDashboardPerm]
    dashboard_perms = {'GET': 'tickets.view'}

    def get_exception_handler(self):
        return partial(custom_exception_handler, keep_fields=True)

    def base_queryset(self):
        qs = Ticket.objects.select_related('client__client_profile', 'site')
        client_id = _small_int(self.request.query_params.get('client'))
        if client_id is not None:
            qs = qs.filter(client_id=client_id)
        return qs


class StaffTicketSummaryView(_StaffView, generics.GenericAPIView):
    def get(self, request):
        return Response(summarise_tickets(self.base_queryset()))


class StaffTicketUnreadView(_StaffView, generics.GenericAPIView):
    """Unread client messages across all tickets (the sidebar's red dot)."""

    def get(self, request):
        return Response(unread_totals(TicketMessage.objects.filter(
            author_role=TicketMessage.Author.CLIENT, id__gt=F('ticket__staff_read_upto'),
        )))


class StaffTicketListView(_StaffView, generics.ListCreateAPIView):
    serializer_class = StaffTicketSerializer
    pagination_class = TicketPagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    dashboard_perms = {'GET': 'tickets.view', 'POST': 'tickets.create'}

    def get_throttles(self):
        return [TicketCreateThrottle()] if self.request.method == 'POST' else super().get_throttles()

    def get_queryset(self):
        qs = with_counts(self.base_queryset(), 'staff')
        return filter_tickets(qs, self.request.query_params,
                              extra_search=_CLIENT_SEARCH, extra_ordering=_CLIENT_ORDERING)

    def create(self, request, *args, **kwargs):
        client = User.objects.filter(
            pk=_small_int(str(request.data.get('client') or '')), client_profile__role='client', is_active=True,
        ).first()
        if client is None:
            return Response({'detail': 'Choose the client this ticket is for.', 'client': ['Choose a client.']},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = TicketCreateSerializer(data=request.data, context={'request': request, 'client': client})
        serializer.is_valid(raise_exception=True)
        try:
            files = validate_attachments(request.FILES.getlist('files'))
        except drf_serializers.ValidationError as exc:
            return Response({'files': exc.detail}, status=status.HTTP_400_BAD_REQUEST)
        ticket = raise_ticket(serializer, client, files)
        logger.info(f'{request.user.username} raised {ticket.reference} for {client.username} ({ticket.urgency})')
        fresh = with_counts(self.base_queryset(), 'staff').get(pk=ticket.pk)
        return Response(StaffTicketSerializer(fresh).data, status=status.HTTP_201_CREATED)


class StaffTicketDetailView(_StaffView, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StaffTicketDetailSerializer
    http_method_names = ['get', 'patch', 'delete', 'options']

    def dashboard_perm(self, request):
        if request.method in ('GET', 'HEAD'):
            return 'tickets.view'
        if request.method == 'DELETE':
            return 'tickets.delete'
        # PATCH: the status needs "edit", the technician needs "assign".
        data = request.data if hasattr(request.data, 'keys') else {}
        codes = []
        if 'status' in data or 'notify_client' in data:
            codes.append('tickets.edit')
        if 'technician_name' in data:
            codes.append('tickets.assign')
        return codes or 'tickets.edit'

    def destroy(self, request, *args, **kwargs):
        ticket = self.get_object()
        reference, client_id = ticket.reference, ticket.client_id
        for attachment in ticket.attachments.all():
            attachment.file.delete(save=False)
        ticket.delete()
        ticket.pk, ticket.client_id = kwargs['pk'], client_id
        ticket_changed(ticket, 'deleted')  # lists refresh on both sides
        logger.warning(f'{request.user.username} deleted ticket {reference}')
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_queryset(self):
        return with_counts(
            Ticket.objects.select_related('client__client_profile', 'site').prefetch_related('attachments'),
            'staff',
        )

    def update(self, request, *args, **kwargs):
        ticket = self.get_object()
        old_status = ticket.status
        serializer = StaffTicketUpdateSerializer(ticket, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        notify = serializer.validated_data.pop('notify_client', True)
        serializer.save()
        ticket_changed(ticket, 'status')

        emailed = False
        if ticket.status != old_status:
            logger.info(f'{request.user.username} moved {ticket.reference} from {old_status} to {ticket.status}')
            if notify:
                emailed = send_status_update(ticket, old_status)

        fresh = self.get_queryset().get(pk=ticket.pk)
        return Response({**self.get_serializer(fresh).data, 'client_emailed': emailed})
