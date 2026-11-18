"""
Tool bookings API (mounted at ``/tools/``): staff only, per permission
(``apps.staff.catalogue`` section ``tools``).

    GET    /tools/                       every tool with its availability and who has it
                                         ?type &availability=available|partly|out|oos &condition &q
                                         &tile=available|out|in_repair|lost_retired (&available=1 = tile=available)
                                         (+ ``summary``: the tile counts over everything, whatever the filters)
    POST   /tools/                       add a tool (tools.create)
    GET    /tools/<id>/                  one tool + the bookings it was in
    PATCH  /tools/<id>/                  edit, incl. condition (tools.edit)
    DELETE /tools/<id>/                  move to the trash (tools.delete), not while any unit is out
    GET    /tools/bookings/              ?status=out|overdue|partly|returned &check=waiting|checked|flagged
                                         &technician=<id>|me &since=week|month|quarter
                                         &tile=out|check|overdue|due_soon
                                         &tool=<id> &q &page &archived=1 | &include_archived=1
                                         (waiting-for-check filters always include archived bookings)
    POST   /tools/bookings/              book out (tools.book); 409 {conflicts} when a tool is out with
                                         someone else, unless ``confirm_handover``
    GET    /tools/bookings/<id>/         one booking with its movements (book-out, returns, checks)
    PATCH  /tools/bookings/<id>/         due date, long-term kit, job, notes (tools.book)
    DELETE /tools/bookings/<id>/         move to the trash (tools.book), only while nothing is returned
    POST   /tools/bookings/<id>/return/  return some or all tools (tools.book) → ``archive_event``
    POST   /tools/bookings/check/        confirm what's waiting on these bookings (tools.check)
    POST   /tools/bookings/<id>/archive/ | unarchive/, /tools/bookings/bulk-archive/ (tools.archive)
    GET    /tools/who-has-what/          tools still out, by technician (the bookings filters apply)
    GET    /tools/options/               types, conditions, staff, settings, can_check
    GET    /tools/settings/              colours, overdue, default due date, alerts; PATCH: tools.settings
    POST   /tools/types/, PATCH /tools/types/<id>/   tool types (tools.settings), never deleted

The rules are in apps.tools.services; every change is in the audit history.
"""

import logging
from datetime import timedelta
from functools import partial

from django.db import transaction
from django.db.models import Max, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.records import archive, audit
from apps.staff.permissions import HasDashboardPerm
from utils.exceptions import custom_exception_handler

from .models import Booking, BookingItem, Movement, Tool, ToolSettings, ToolType, format_booking_id, format_tool_id
from .serializers import (
    BookingUpdateSerializer, BookOutSerializer, CheckSerializer, ReturnSerializer, ToolSettingsSerializer,
    ToolTypeSerializer, ToolWriteSerializer, booking_detail, booking_row, person, staff_queryset, tool_row,
)
from .services import (
    HandoverNeeded, booking_state, book_out, can_check, confirm, due_soon_q, next_number, open_items, overdue_q,
    return_items, save_with_next_number, status_q, units_out, with_flags,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
SINCE_DAYS = {'week': 7, 'month': 30, 'quarter': 92}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bookings():
    return Booking.objects.select_related('technician', 'job', 'created_by', 'archived_by').prefetch_related(
        Prefetch('items', queryset=BookingItem.objects.select_related('tool').order_by('id')),
        Prefetch('movements', queryset=Movement.objects.select_related('recorded_by', 'checked_by')),
    )


def _holders(tool_ids=None):
    """{tool id: [open items]} with their booking and technician."""
    qs = open_items().select_related('booking__technician')
    if tool_ids is not None:
        qs = qs.filter(tool_id__in=list(tool_ids))
    found = {}
    for item in qs:
        found.setdefault(item.tool_id, []).append(item)
    return found


def summary(settings, today=None):
    """The tiles of both pages, over everything (filters don't change them).

    Bookings: out_units (+ out_people, out_bookings), waiting, overdue, due_soon.
    Tools: available (of in_service), tools_out, in_repair, lost_retired.
    """
    today = today or timezone.localdate()
    out = list(open_items().values('quantity', 'booking_id', 'booking__technician_id'))
    all_tools = list(Tool.objects.only('pk', 'quantity', 'condition'))
    tools = [t for t in all_tools if t.condition == Tool.Condition.IN_SERVICE]
    out_by_tool = units_out()
    flagged = with_flags(Booking.objects.all())
    return {
        'out_units': sum(r['quantity'] for r in out),
        'out_people': len({r['booking__technician_id'] for r in out}),
        'out_bookings': len({r['booking_id'] for r in out}),
        # Archived bookings too: a return can still wait for its check after the booking is archived.
        'waiting': flagged.filter(waiting=True).count(),
        'overdue': flagged.filter(overdue_q(settings, today)).count(),
        'due_soon': flagged.filter(due_soon_q(today)).count(),
        'available': sum(1 for t in tools if t.quantity > out_by_tool.get(t.pk, 0)),
        'in_service': len(tools),
        'tools_out': sum(1 for t in all_tools if out_by_tool.get(t.pk, 0)),
        'in_repair': sum(1 for t in all_tools if t.condition == Tool.Condition.IN_REPAIR),
        'lost_retired': sum(1 for t in all_tools if t.condition in (Tool.Condition.LOST, Tool.Condition.RETIRED)),
    }


class _ToolsView(APIView):
    permission_classes = [HasDashboardPerm]

    def get_exception_handler(self):
        return partial(custom_exception_handler, keep_fields=True)


# ── tools ────────────────────────────────────────────────────────────────────

class ToolListView(_ToolsView):
    dashboard_perms = {'GET': 'tools.view', 'POST': 'tools.create'}

    def get(self, request):
        p = request.query_params
        tools = list(Tool.objects.select_related('type'))
        out = units_out()
        holders = _holders()
        rows = [tool_row(t, out.get(t.pk, 0), holders.get(t.pk, [])) for t in tools]
        counts = {'all': len(rows), 'not_retired': sum(1 for r in rows if r['condition'] != Tool.Condition.RETIRED)}

        if _int(p.get('type')) is not None:
            rows = [r for r in rows if r['type'] == _int(p['type'])]
        if p.get('availability') in ('available', 'partly', 'out', 'oos'):
            rows = [r for r in rows if r['availability'] == p['availability']]
        if p.get('condition') in Tool.Condition.values:
            rows = [r for r in rows if r['condition'] == p['condition']]
        tile = 'available' if p.get('available') else p.get('tile')
        if tile == 'available':
            rows = [r for r in rows if r['free'] > 0]
        elif tile == 'out':
            rows = [r for r in rows if r['units_out'] > 0]
        elif tile == 'in_repair':
            rows = [r for r in rows if r['condition'] == Tool.Condition.IN_REPAIR]
        elif tile == 'lost_retired':
            rows = [r for r in rows if r['condition'] in (Tool.Condition.LOST, Tool.Condition.RETIRED)]
        q = ' '.join((p.get('q') or '').split()).lower()
        if q:
            def hit(r):
                text = ' '.join([r['name'], r['brand'], r['model'], r['serial'], r['tool_id'], r['type_label'],
                                 *(h['technician']['name'] for h in r['holders'])]).lower()
                return all(word in text for word in q.split())
            rows = [r for r in rows if hit(r)]
        return Response({'results': rows, 'count': len(rows), 'counts': counts,
                         'summary': summary(ToolSettings.load())})

    @transaction.atomic
    def post(self, request):
        serializer = ToolWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tool = Tool(**serializer.validated_data, created_by=request.user)
        save_with_next_number(tool)
        audit.log_create(request.user, tool)
        logger.info(f'{request.user.username} added tool {tool.tool_id}')
        return Response(tool_detail(tool), status=status.HTTP_201_CREATED)


def tool_detail(tool):
    tool = Tool.all_objects.select_related('type', 'created_by').get(pk=tool.pk)
    out = units_out([tool.pk]).get(tool.pk, 0)
    settings = ToolSettings.load()
    bookings = _bookings().filter(items__tool=tool).distinct().order_by('-date_out', '-number')[:200]
    return {
        **tool_row(tool, out, _holders([tool.pk]).get(tool.pk, [])),
        'created_by': person(tool.created_by),
        'created_at': tool.created_at,
        'bookings': [booking_row(b, settings) for b in bookings],
    }


class ToolDetailView(_ToolsView):
    dashboard_perms = {'GET': 'tools.view', 'PATCH': 'tools.edit', 'DELETE': 'tools.delete'}

    def get(self, request, pk):
        return Response(tool_detail(get_object_or_404(Tool, pk=pk)))

    @transaction.atomic
    def patch(self, request, pk):
        tool = get_object_or_404(Tool.objects.select_for_update(), pk=pk)
        out = units_out([tool.pk]).get(tool.pk, 0)
        serializer = ToolWriteSerializer(tool, data=request.data, partial=True, context={'units_out': out})
        serializer.is_valid(raise_exception=True)
        before = audit.snapshot(tool)
        serializer.save()
        audit.log_update(request.user, tool, before)
        return Response(tool_detail(tool))

    @transaction.atomic
    def delete(self, request, pk):
        tool = get_object_or_404(Tool.objects.select_for_update(), pk=pk)
        if units_out([tool.pk]).get(tool.pk):
            return Response({'detail': f'{tool.name} is booked out. Return it before deleting it.'},
                            status=status.HTTP_400_BAD_REQUEST)
        tool.trash(request.user)
        logger.info(f'{request.user.username} moved tool {tool.tool_id} to the trash')
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── bookings ─────────────────────────────────────────────────────────────────

def filter_bookings(qs, request, settings, today):
    """The bookings list filters (also used by Who has what)."""
    p = request.query_params
    qs = with_flags(qs)
    waiting_view = p.get('tile') == 'check' or p.get('check') == 'waiting'
    if not waiting_view:  # returns waiting for their check stay visible after the booking is archived
        qs = archive.filter_archived(qs, p)
    tile = p.get('tile')
    if tile == 'out':
        qs = qs.filter(has_open=True)
    elif tile == 'check':
        qs = qs.filter(waiting=True)
    elif tile == 'overdue':
        qs = qs.filter(overdue_q(settings, today))
    elif tile == 'due_soon':
        qs = qs.filter(due_soon_q(today))
    q_status = status_q(p.get('status'), settings, today)
    if q_status is not None:
        qs = qs.filter(q_status)
    check = p.get('check')
    if check == 'waiting':
        qs = qs.filter(waiting=True)
    elif check == 'checked':
        qs = qs.filter(waiting=False)
    elif check == 'flagged':
        qs = qs.filter(flagged=True)
    technician = p.get('technician')
    if technician == 'me':
        qs = qs.filter(technician=request.user)
    elif _int(technician) is not None:
        qs = qs.filter(technician_id=_int(technician))
    if p.get('since') in SINCE_DAYS:
        qs = qs.filter(date_out__gte=today - timedelta(days=SINCE_DAYS[p['since']]))
    if _int(p.get('tool')) is not None:
        qs = qs.filter(items__tool_id=_int(p['tool']))
    q = ' '.join((p.get('q') or '').split())
    if q:
        for word in q.split():
            match = (Q(technician__first_name__icontains=word) | Q(technician__last_name__icontains=word)
                     | Q(technician__username__icontains=word) | Q(notes__icontains=word)
                     | Q(items__tool_label__icontains=word) | Q(items__tool__name__icontains=word)
                     | Q(items__tool__brand__icontains=word) | Q(items__tool__serial__icontains=word))
            upper = word.upper()
            if upper.startswith('TB') and upper.removeprefix('TB').lstrip('-').isdigit():
                match |= Q(number=int(upper.removeprefix('TB').lstrip('-')))
            elif upper.startswith('APS') and upper.removeprefix('APS').lstrip('-').isdigit():
                match |= Q(job__number=int(upper.removeprefix('APS').lstrip('-')))
            elif word.isdigit():
                match |= Q(number=int(word)) | Q(job__number=int(word))
            qs = qs.filter(match)
        qs = qs.distinct()
    return qs


class BookingListView(_ToolsView):
    dashboard_perms = {'GET': 'tools.view', 'POST': 'tools.book'}

    def get(self, request):
        p = request.query_params
        settings = ToolSettings.load()
        today = timezone.localdate()
        qs = filter_bookings(_bookings(), request, settings, today)
        qs = qs.order_by('-has_open', '-date_out', '-number')
        total = qs.count()
        pages = max(1, -(-total // PAGE_SIZE))
        page = min(max(1, _int(p.get('page')) or 1), pages)
        rows = [booking_row(b, settings, today) for b in qs[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]]
        return Response({'results': rows, 'count': total, 'page': page, 'total_pages': pages,
                         'summary': summary(settings, today),
                         'total': Booking.objects.filter(archived_at__isnull=True).count(),
                         'archived_count': archive.archived_count(Booking.objects.all())})

    def post(self, request):
        serializer = BookOutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        try:
            booking, events = book_out(
                request.user, technician=d['technician'], items=[(i['tool'], i['quantity']) for i in d['items']],
                date_out=d['date_out'], due_date=d.get('due_date'), long_term_kit=d['long_term_kit'],
                job=d.get('job'), notes=d['notes'], notify=d['notify'], confirm_handover=d['confirm_handover'])
        except HandoverNeeded as exc:
            names = ', '.join(f"{c['name']} ({c['holder']})" for c in exc.conflicts)
            return Response({'detail': f'Already out with someone else: {names}. Book it only if it was handed over.',
                             'code': 'handover', 'conflicts': exc.conflicts}, status=status.HTTP_409_CONFLICT)
        logger.info(f'{request.user.username} booked out {booking.booking_id}')
        data = booking_detail(_bookings().get(pk=booking.pk), ToolSettings.load())
        return Response({**data, 'handover_events': events}, status=status.HTTP_201_CREATED)


class BookingDetailView(_ToolsView):
    dashboard_perms = {'GET': 'tools.view', 'PATCH': 'tools.book', 'DELETE': 'tools.book'}

    def get(self, request, pk):
        return Response(booking_detail(get_object_or_404(_bookings(), pk=pk), ToolSettings.load()))

    @transaction.atomic
    def patch(self, request, pk):
        booking = get_object_or_404(Booking.objects.select_for_update(), pk=pk)
        serializer = BookingUpdateSerializer(booking, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = audit.snapshot(booking)
        for field, value in serializer.validated_data.items():
            setattr(booking, field, value)
        booking.save()
        audit.log_update(request.user, booking, before)
        return Response(booking_detail(_bookings().get(pk=booking.pk), ToolSettings.load()))

    @transaction.atomic
    def delete(self, request, pk):
        booking = get_object_or_404(Booking.objects.select_for_update(), pk=pk)
        if booking.items.filter(return_movement__isnull=False).exists():
            return Response({'detail': 'Some tools on this booking are already returned, so it can’t be deleted. '
                                       'Archive it instead.'}, status=status.HTTP_400_BAD_REQUEST)
        booking.trash(request.user)
        logger.info(f'{request.user.username} moved {booking.booking_id} to the trash')
        return Response(status=status.HTTP_204_NO_CONTENT)


class BookingReturnView(_ToolsView):
    dashboard_perms = {'POST': 'tools.book'}

    @transaction.atomic
    def post(self, request, pk):
        booking = get_object_or_404(Booking.objects.select_for_update(), pk=pk)
        serializer = ReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        items = {i.pk: i for i in booking.items.all()}
        lines = []
        for line in d['items']:
            item = items.get(line['item'])
            if item is None:
                return Response({'items': ['One of the tools isn’t on this booking. Reload and try again.']},
                                status=status.HTTP_400_BAD_REQUEST)
            lines.append({**line, 'item': item})
        _, event = return_items(request.user, booking, lines, date=d['date'], notify=d['notify'])
        data = booking_detail(_bookings().get(pk=booking.pk), ToolSettings.load())
        return Response({**data, 'archive_event': event})


class BookingCheckView(_ToolsView):
    dashboard_perms = {'POST': 'tools.check'}

    def post(self, request):
        serializer = CheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        ids = list(dict.fromkeys(d['bookings']))
        with transaction.atomic():
            found = Booking.objects.select_for_update().in_bulk(ids)
            done, skipped = confirm(request.user, [found[i] for i in ids if i in found],
                                    note=d['note'], problem=d['problem'])
        skipped += [i for i in ids if i not in found]
        return Response({'done': done, 'skipped': skipped})


class BookingArchiveView(archive.ArchiveView):
    section = 'tools'

    def get_item(self, pk):
        return get_object_or_404(Booking.objects.select_for_update(), pk=pk)

    @transaction.atomic
    def post(self, request, **kwargs):
        if self.archived and self.get_item(**kwargs).has_open_items():
            return Response({'detail': 'Some tools on this booking are still out. Return them before archiving it.'},
                            status=status.HTTP_400_BAD_REQUEST)
        return super().post(request, **kwargs)


class BookingUnarchiveView(BookingArchiveView):
    archived = False


class BookingBulkArchiveView(archive.BulkArchiveView):
    section = 'tools'

    def find(self, ids):
        found = Booking.objects.select_for_update().in_bulk(ids)
        if self.request.data.get('archived') is True:  # only fully returned bookings can be archived
            found = {k: b for k, b in found.items() if not b.has_open_items()}
        return found


class WhoHasWhatView(_ToolsView):
    dashboard_perms = {'GET': 'tools.view'}

    def get(self, request):
        settings = ToolSettings.load()
        today = timezone.localdate()
        qs = filter_bookings(_bookings(), request, settings, today).filter(has_open=True).order_by('date_out', 'number')
        groups = {}
        for booking in qs:
            state = booking_state(booking, settings, today)
            group = groups.setdefault(booking.technician_id, {
                'technician': person(booking.technician), 'units': 0, 'since': booking.date_out,
                'long_term_kit': False, 'waiting': False, 'level': 0, 'items': []})
            group['units'] += sum(i.quantity for i in state['open'])
            group['since'] = min(group['since'], booking.date_out)
            group['long_term_kit'] |= booking.long_term_kit
            group['waiting'] |= bool(state['waiting'])
            group['level'] = max(group['level'], state['level'])
            row = booking_row(booking, settings, today)
            for item in state['open']:
                group['items'].append({
                    'item': item.pk, 'tool': row['items'][[i['id'] for i in row['items']].index(item.pk)]['tool'],
                    'quantity': item.quantity, 'booking': booking.pk, 'booking_id': booking.booking_id,
                    'date_out': booking.date_out, 'due_date': booking.due_date,
                    'long_term_kit': booking.long_term_kit, 'days_out': state['days_out'], 'level': state['level'],
                    'overdue': state['overdue'], 'check': row['check'],
                })
        result = sorted(groups.values(), key=lambda g: g['technician']['name'].lower())
        return Response({'results': result, 'count': len(result)})


# ── options, settings, types ─────────────────────────────────────────────────

class ToolOptionsView(_ToolsView):
    """Everything the forms and filters need, in one call."""

    dashboard_perms = {'GET': 'tools.view'}

    def get(self, request):
        settings = ToolSettings.load()
        # People who've had tools most recently first, for the book-out picker.
        recent = {row['technician']: row['last'] for row in
                  Booking.objects.values('technician').annotate(last=Max('date_out'))}
        staff = sorted(({'id': u.pk, 'name': person(u)['name'], 'email': bool(u.email),
                         'last_booking': recent.get(u.pk)} for u in staff_queryset()),
                       key=lambda s: s['name'].lower())
        return Response({
            'types': ToolTypeSerializer(ToolType.objects.all(), many=True).data,
            'conditions': [{'value': v, 'label': lab} for v, lab in Tool.Condition.choices],
            'return_conditions': [{'value': v, 'label': lab} for v, lab in BookingItem.Condition.choices],
            'staff': staff,
            'settings': ToolSettingsSerializer(settings).data,
            'overdue_days': settings.overdue_days,
            'can_check': can_check(request.user),
            'next_tool_id': format_tool_id(next_number(Tool)),
            'next_booking_id': format_booking_id(next_number(Booking)),
            'today': timezone.localdate(),
        })


class ToolSettingsView(_ToolsView):
    dashboard_perms = {'GET': 'tools.view', 'PATCH': 'tools.settings'}

    def get(self, request):
        return Response(ToolSettingsSerializer(ToolSettings.load()).data)

    @transaction.atomic
    def patch(self, request):
        row = ToolSettings.load()
        serializer = ToolSettingsSerializer(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = audit.snapshot(row)
        serializer.save()
        audit.log_update(request.user, row, before)
        return Response(serializer.data)


class ToolTypeListView(_ToolsView):
    dashboard_perms = {'POST': 'tools.settings'}

    @transaction.atomic
    def post(self, request):
        serializer = ToolTypeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        last = ToolType.objects.aggregate(n=Max('order'))['n']
        tool_type = serializer.save(order=serializer.validated_data.get('order', (last or 0) + 1))
        audit.log_create(request.user, tool_type)
        return Response(ToolTypeSerializer(tool_type).data, status=status.HTTP_201_CREATED)


class ToolTypeDetailView(_ToolsView):
    dashboard_perms = {'PATCH': 'tools.settings'}

    @transaction.atomic
    def patch(self, request, pk):
        tool_type = get_object_or_404(ToolType, pk=pk)
        serializer = ToolTypeSerializer(tool_type, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = audit.snapshot(tool_type)
        serializer.save()
        audit.log_update(request.user, tool_type, before)
        return Response(ToolTypeSerializer(tool_type).data)
