"""
Staff side of client tickets (mounted at ``/client-portal/staff/``) — the
dashboard's "Client tickets" pages. Dashboard admins only
(``apps.core.permissions.IsDashboardAdmin``); clients get 403.

- ``GET   staff/tickets/summary/`` → counts per status group (optional ``?client=``).
- ``GET   staff/tickets/``         → every client's tickets: the client list's
  filters (status_group, status, service, urgency, site, search, ordering, page)
  plus ``client=<user id>``; search also matches the client's name, username
  and email; ``ordering=client`` sorts by client name.
- ``GET   staff/tickets/<id>/``    → one ticket with its client and attachments.
- ``PATCH staff/tickets/<id>/``    → {status?, technician_name?, notify_client?}.
  A status change emails the client (to a confirmed email only) unless
  ``notify_client`` is false.
"""

import logging
from functools import partial

from rest_framework import generics
from rest_framework.response import Response

from apps.core.permissions import IsDashboardAdmin
from utils.exceptions import custom_exception_handler

from .emails import send_status_update
from django.db.models import F

from .models import Ticket, TicketMessage
from .serializers import StaffTicketDetailSerializer, StaffTicketSerializer, StaffTicketUpdateSerializer
from .views import TicketPagination, _small_int, filter_tickets, summarise_tickets, unread_totals, with_counts

logger = logging.getLogger('apps.client_portal')

_CLIENT_SEARCH = (
    'client__client_profile__company_name__icontains',
    'client__username__icontains',
    'client__email__icontains',
)
_CLIENT_ORDERING = {'client': 'client__client_profile__company_name', 'urgency': 'urgency'}


class _StaffView:
    permission_classes = [IsDashboardAdmin]

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


class StaffTicketListView(_StaffView, generics.ListAPIView):
    serializer_class = StaffTicketSerializer
    pagination_class = TicketPagination

    def get_queryset(self):
        qs = with_counts(self.base_queryset(), 'staff')
        return filter_tickets(qs, self.request.query_params,
                              extra_search=_CLIENT_SEARCH, extra_ordering=_CLIENT_ORDERING)


class StaffTicketDetailView(_StaffView, generics.RetrieveUpdateAPIView):
    serializer_class = StaffTicketDetailSerializer
    http_method_names = ['get', 'patch', 'options']

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

        emailed = False
        if ticket.status != old_status:
            logger.info(f'{request.user.username} moved {ticket.reference} from {old_status} to {ticket.status}')
            if notify:
                emailed = send_status_update(ticket, old_status)

        fresh = self.get_queryset().get(pk=ticket.pk)
        return Response({**self.get_serializer(fresh).data, 'client_emailed': emailed})
