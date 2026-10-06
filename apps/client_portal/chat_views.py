"""
Ticket chat — one conversation per ticket between the client and APS staff.

No websockets: an open ticket page polls ``GET …/messages/?after=<last id>``
every few seconds and gets only what's new, plus the ticket's live status (so a
status change shows up without a reload) and how far the other side has read
(for the "Seen" mark).

- Client: ``/client-portal/tickets/<id>/messages/`` (own tickets only; another
  client's ticket is a 404).
- Staff:  ``/client-portal/staff/tickets/<id>/messages/`` (any ticket).

``GET`` params: ``after`` (message id, default 0 = the whole conversation) and
``read=1`` to mark everything returned as seen (the page sends it only while
it's actually on screen). ``POST`` {body} adds a message and bumps the ticket's
"last update".

Nobody is emailed per message: a job every 30 minutes emails each side about
messages they still haven't read (see digests.py).
"""

import logging
from functools import partial

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response

from apps.core.permissions import IsDashboardAdmin, IsDashboardClient
from utils.exceptions import custom_exception_handler

from .models import Ticket, TicketMessage
from .serializers import TicketMessageCreateSerializer, TicketMessageSerializer
from .throttling import MessageWriteThrottle
from .views import _small_int

logger = logging.getLogger('apps.client_portal')

# Most messages returned by one GET (the whole history on first load).
MAX_MESSAGES = 500

_READ_FIELD = {'client': 'client_read_upto', 'staff': 'staff_read_upto'}
_OTHER = {'client': 'staff', 'staff': 'client'}


class _ChatView(generics.GenericAPIView):
    viewer_role = ''  # 'client' | 'staff'
    throttle_classes = [MessageWriteThrottle]

    def get_exception_handler(self):
        return partial(custom_exception_handler, keep_fields=True)

    def ticket_queryset(self):
        raise NotImplementedError

    def get_ticket(self, pk):
        return get_object_or_404(self.ticket_queryset().select_related('client__client_profile'), pk=pk)

    def _payload(self, ticket, messages):
        other_field = _READ_FIELD[_OTHER[self.viewer_role]]
        return {
            'messages': TicketMessageSerializer(messages, many=True, context={'viewer_role': self.viewer_role}).data,
            # Messages of mine with id <= this have been seen by the other side.
            'other_read_upto': getattr(ticket, other_field),
            'ticket': {
                'status': ticket.status,
                'status_label': ticket.get_status_display(),
                'status_group': ticket.status_group,
                'technician_name': ticket.technician_name,
                'updated_at': ticket.updated_at,
            },
        }

    def get(self, request, pk):
        ticket = self.get_ticket(pk)
        after = _small_int(request.query_params.get('after')) or 0
        messages = list(
            ticket.messages.filter(id__gt=after).select_related('author').order_by('-id')[:MAX_MESSAGES]
        )[::-1]
        if messages and request.query_params.get('read') == '1':
            field = _READ_FIELD[self.viewer_role]
            newest = messages[-1].pk
            # Only ever moves forward, even if two tabs race.
            Ticket.objects.filter(pk=ticket.pk, **{f'{field}__lt': newest}).update(**{field: newest})
        return Response(self._payload(ticket, messages))

    def post(self, request, pk):
        ticket = self.get_ticket(pk)
        serializer = TicketMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        field = _READ_FIELD[self.viewer_role]
        with transaction.atomic():
            message = TicketMessage.objects.create(
                ticket=ticket, author=request.user, author_role=self.viewer_role,
                body=serializer.validated_data['body'],
            )
            # The sender has obviously seen their own message; the ticket's
            # "last update" moves so busy conversations rise in the lists.
            Ticket.objects.filter(pk=ticket.pk).update(**{field: message.pk}, updated_at=timezone.now())
        logger.info(f'{request.user.username} ({self.viewer_role}) wrote on {ticket.reference}')
        ticket.refresh_from_db()
        return Response(
            {**self._payload(ticket, []),
             'message': TicketMessageSerializer(message, context={'viewer_role': self.viewer_role}).data},
            status=status.HTTP_201_CREATED,
        )


class ClientTicketMessagesView(_ChatView):
    viewer_role = 'client'
    permission_classes = [IsDashboardClient]

    def ticket_queryset(self):
        return Ticket.objects.filter(client=self.request.user)


class StaffTicketMessagesView(_ChatView):
    viewer_role = 'staff'
    permission_classes = [IsDashboardAdmin]

    def ticket_queryset(self):
        return Ticket.objects.all()

