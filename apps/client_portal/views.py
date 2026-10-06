"""
Client dashboard API: tickets, sites and subscriptions (mounted at
``/client-portal/``).

Signed in with the normal dashboard JWT (``/dashboard/auth/token/`` — see
apps.accounts); every endpoint is for client accounts only (staff get 403):
- ``GET  me/``              → the client and the service list.
- ``GET  sites/``           → the client's active sites (with ticket/report counts).
- ``POST sites/``           → add a site {name, address}.
- ``PATCH sites/<id>/``     → rename a site / change its address.
- ``GET  subscriptions/``            → recurring plans paid under the account's
  verified email (both APS subscription apps — see apps.subscription_portal).
- ``GET  subscriptions/payments/``   → that email's confirmed payment history.
- ``POST subscriptions/cancel/``     → cancel one plan {ref} at PayFast + locally.
- ``GET  tickets/summary/`` → ticket counts per status group (the stat cards).
- ``GET  tickets/``         → the client's tickets: filter, search, sort, paginate.
- ``POST tickets/``         → raise a ticket (multipart, with up to 10 files).

Every ticket and site query is scoped to ``request.user`` — a client can never
read or target another client's rows. Subscriptions are matched on the
account's email only once that email is verified (a link was clicked), so
typing someone else's address into an account never shows their payments.
"""

import logging
from functools import partial

from django.db import transaction
from django.db.models import Count, F, Q
from django.urls import reverse
from rest_framework import generics, serializers as drf_serializers, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.models import ClientProfile
from apps.core.permissions import IsDashboardClient
from apps.services_and_projects.models import Service
from apps.solar_dashboard.models import Site
from apps.subscription_portal.cancellation import cancel_for_email
from apps.subscription_portal.providers import gather_payments, gather_subscriptions
from utils.exceptions import custom_exception_handler

from .emails import notify_team_new_ticket, ping_telegram_emergency, send_ticket_confirmation
from .models import Ticket, TicketAttachment, TicketMessage
from .serializers import (
    ClientTicketDetailSerializer,
    PortalSiteSerializer,
    TicketCreateSerializer,
    TicketSerializer,
    validate_attachments,
)
from .throttling import SiteWriteThrottle, TicketCreateThrottle

logger = logging.getLogger('apps.client_portal')

# `?ordering=` value → model field. Anything else falls back to the default.
_ORDERING_FIELDS = {
    'reference': 'id',
    'title': 'title',
    'service': 'service',
    'site': 'site__name',
    'status': 'status',
    'technician': 'technician_name',
    'created': 'created_at',
    'updated': 'updated_at',
}
_DEFAULT_ORDERING = ['-updated_at', '-id']


def _body(request):
    """The request body as a dict; a JSON array/scalar body counts as empty."""
    return request.data if isinstance(request.data, dict) else {}


def _small_int(value):
    """``"12"`` → 12; anything else (``"²"``, 40 digits, ``None``) → None."""
    value = (value or '').strip()
    if value.isascii() and value.isdigit() and len(value) <= 9:
        return int(value)
    return None


def filter_tickets(qs, params, *, extra_search=(), extra_ordering=None):
    """Apply the ticket list's query params (tab, filters, search, sort).

    Shared by the client list and the staff list; staff also search the
    client's name/email (``extra_search`` lookups) and can sort by client.
    """
    group = params.get('status_group')
    if group in Ticket.STATUS_GROUPS:
        qs = qs.filter(status__in=Ticket.STATUS_GROUPS[group])
    if params.get('status') in Ticket.Status.values:
        qs = qs.filter(status=params['status'])
    service = (params.get('service') or '').replace('\x00', '').strip()
    if service:
        qs = qs.filter(service__iexact=service)
    if params.get('urgency') in Ticket.Urgency.values:
        qs = qs.filter(urgency=params['urgency'])
    site_id = _small_int(params.get('site'))
    if site_id is not None:
        qs = qs.filter(site_id=site_id)

    # NUL bytes are dropped: Postgres refuses them in a query (→ 500).
    search = (params.get('search') or '').replace('\x00', '').strip()
    if search:
        match = (
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(site__name__icontains=search)
            | Q(technician_name__icontains=search)
            | Q(service__icontains=search)
        )
        for lookup in extra_search:
            match |= Q(**{lookup: search})
        pk = Ticket.pk_from_reference(search)
        if pk:
            match |= Q(pk=pk)
        qs = qs.filter(match)

    fields = {**_ORDERING_FIELDS, **(extra_ordering or {})}
    ordering = (params.get('ordering') or '').strip()
    field = fields.get(ordering.lstrip('-'))
    if field:
        prefix = '-' if ordering.startswith('-') else ''
        # Tie-break on id so pages don't shuffle rows with equal sort keys.
        return qs.order_by(f'{prefix}{field}', f'{prefix}id')
    return qs.order_by(*_DEFAULT_ORDERING)


def with_counts(qs, viewer_role):
    """Annotate attachment_count and unread_count (chat messages from the other
    side newer than the viewer's read marker). Both distinct: two joins."""
    other = 'staff' if viewer_role == 'client' else 'client'
    marker = 'client_read_upto' if viewer_role == 'client' else 'staff_read_upto'
    return qs.annotate(
        attachment_count=Count('attachments', distinct=True),
        unread_count=Count(
            'messages',
            filter=Q(messages__author_role=other, messages__id__gt=F(marker)),
            distinct=True,
        ),
    )


def summarise_tickets(qs):
    """Ticket counts per status group, plus 'all' (the stat cards)."""
    counts = qs.aggregate(**{
        group: Count('id', filter=Q(status__in=statuses))
        for group, statuses in Ticket.STATUS_GROUPS.items()
    })
    counts['all'] = sum(counts.values())
    return counts


def service_options(request):
    """APS's services for the new-ticket form, live from Site content, in the public site's order."""
    return [
        {'id': s.pk, 'title': s.title, 'image': request.build_absolute_uri(s.image.url) if s.image else None}
        for s in Service.objects.order_by('-appreciation_mark', 'title')
    ]


def _profile(user):
    try:
        return user.client_profile
    except ClientProfile.DoesNotExist:
        return None


def verified_email(user):
    """The account's email if it's been confirmed, else ''."""
    profile = _profile(user)
    return user.email if profile and profile.email_verified else ''


def client_name(user):
    profile = _profile(user)
    return (profile.company_name if profile else '') or user.get_username()


class _ClientView:
    permission_classes = [IsDashboardClient]

    def get_exception_handler(self):
        # The project handler flattens errors to {"detail"}; these forms need
        # the field names too so they can highlight the right input.
        return partial(custom_exception_handler, keep_fields=True)


class MeView(_ClientView, generics.GenericAPIView):
    def get(self, request):
        user = request.user
        profile = _profile(user)
        return Response({
            'contact': {'name': client_name(user), 'email': user.email, 'phone': profile.phone if profile else ''},
            'company': {'name': client_name(user)},
            'email_verified': bool(verified_email(user)),
            'services': service_options(request),
        })


class _SiteQuerysetMixin:
    serializer_class = PortalSiteSerializer
    throttle_classes = [SiteWriteThrottle]

    def get_queryset(self):
        # Retired sites stay out of the dashboard (APS manages them); old
        # tickets still show their site name through the ticket itself.
        not_closed = ~Q(tickets__status__in=Ticket.STATUS_GROUPS['completed'])
        return (
            Site.objects.filter(client=self.request.user, is_active=True)
            .annotate(
                ticket_count=Count('tickets', distinct=True),
                open_ticket_count=Count('tickets', filter=not_closed, distinct=True),
                report_count=Count('data_rows__report', distinct=True),
            )
            .order_by('order', 'name')
        )


class SiteListCreateView(_ClientView, _SiteQuerysetMixin, generics.ListCreateAPIView):
    # A client has a handful of sites; the pickers need them all at once.
    pagination_class = None

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        site = serializer.save(client=request.user, created_by=request.user)
        logger.info(f'{request.user.username} added site "{site.name}"')
        return Response(
            self.get_serializer(self.get_queryset().get(pk=site.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class SiteDetailView(_ClientView, _SiteQuerysetMixin, generics.UpdateAPIView):
    http_method_names = ['patch', 'options']

    def update(self, request, *args, **kwargs):
        site = self.get_object()
        serializer = self.get_serializer(site, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(self.get_serializer(self.get_queryset().get(pk=site.pk)).data)


class SubscriptionListView(_ClientView, generics.GenericAPIView):
    def get(self, request):
        email = verified_email(request.user)
        return Response({
            'email': email,
            'email_verified': bool(email),
            'subscriptions': [sub.as_dict() for sub in gather_subscriptions(email)] if email else [],
        })


class SubscriptionPaymentsView(_ClientView, generics.GenericAPIView):
    def get(self, request):
        email = verified_email(request.user)
        return Response({
            'email': email,
            'email_verified': bool(email),
            'payments': [payment.as_dict() for payment in gather_payments(email)] if email else [],
        })


class SubscriptionCancelView(_ClientView, generics.GenericAPIView):
    def post(self, request):
        email = verified_email(request.user)
        if not email:
            return Response({'detail': 'Confirm your email first to manage subscriptions.'},
                            status=status.HTTP_403_FORBIDDEN)
        ref = _body(request).get('ref')
        ref = ref if isinstance(ref, str) else None  # missing / non-string → the "required" 400
        code, detail = cancel_for_email(email, ref, source='Client dashboard')
        return Response({'detail': detail}, status=code)


class TicketSummaryView(_ClientView, generics.GenericAPIView):
    def get(self, request):
        return Response(summarise_tickets(Ticket.objects.filter(client=request.user)))


def unread_totals(messages):
    """``{'messages': n, 'tickets': n}`` for a queryset of unread messages."""
    return {'messages': messages.count(), 'tickets': messages.values('ticket_id').distinct().count()}


class TicketUnreadView(_ClientView, generics.GenericAPIView):
    """Unread APS replies across the client's tickets (the sidebar's red dot)."""

    def get(self, request):
        return Response(unread_totals(TicketMessage.objects.filter(
            ticket__client=request.user, author_role=TicketMessage.Author.STAFF,
            id__gt=F('ticket__client_read_upto'),
        )))


class TicketPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50

    def get_paginated_response(self, data):
        return Response({
            'count': self.page.paginator.count,
            'page': self.page.number,
            'page_size': self.get_page_size(self.request),
            'total_pages': self.page.paginator.num_pages,
            'results': data,
        })


class TicketListCreateView(_ClientView, generics.ListCreateAPIView):
    pagination_class = TicketPagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [TicketCreateThrottle]

    def get_serializer_class(self):
        return TicketCreateSerializer if self.request.method == 'POST' else TicketSerializer

    def get_queryset(self):
        qs = with_counts(Ticket.objects.filter(client=self.request.user).select_related('site'), 'client')
        return filter_tickets(qs, self.request.query_params)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            files = validate_attachments(request.FILES.getlist('files'))
        except drf_serializers.ValidationError as exc:
            return Response({'files': exc.detail}, status=status.HTTP_400_BAD_REQUEST)

        client = request.user
        with transaction.atomic():
            ticket = serializer.save(client=client)
            for upload, content_type in files:
                TicketAttachment.objects.create(
                    ticket=ticket,
                    file=upload,
                    original_name=upload.name[:255],
                    content_type=content_type,
                    size=upload.size,
                )

            admin_url = request.build_absolute_uri(
                reverse('admin:client_portal_ticket_change', args=[ticket.pk])
            )
            transaction.on_commit(lambda: _notify_new_ticket(ticket.pk, admin_url))

        ticket = self.get_queryset().get(pk=ticket.pk)
        logger.info(f'{client.username} raised {ticket.reference} ({ticket.urgency})')
        return Response(TicketSerializer(ticket).data, status=status.HTTP_201_CREATED)


class ClientTicketDetailView(_ClientView, generics.RetrieveAPIView):
    """One of the client's own tickets (another client's is a 404)."""

    serializer_class = ClientTicketDetailSerializer

    def get_queryset(self):
        return with_counts(
            Ticket.objects.filter(client=self.request.user).select_related('site').prefetch_related('attachments'),
            'client',
        )


def _notify_new_ticket(ticket_pk, admin_url):
    ticket = Ticket.objects.select_related('client__client_profile', 'site').get(pk=ticket_pk)
    notify_team_new_ticket(ticket, admin_url)
    send_ticket_confirmation(ticket)
    ping_telegram_emergency(ticket)
