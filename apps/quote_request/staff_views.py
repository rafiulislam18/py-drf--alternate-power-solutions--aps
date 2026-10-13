"""
Quote requests in the staff dashboard (read-only for now).

One list across the two places quotes come in from the public site:

- ``general``   — the website quote form (``QuoteRequest``), any service but solar;
                  "General" in the dashboard
- ``solar``     — the same form for "Solar PV System Design & Installation",
                  with its ``SolarQuoteDetails``
- ``container`` — container conversion requests (``container_conversion.ServiceRequest``)

``GET /quote-request/staff/``  ?kind=general|solar|container  &status=new|handled
&q=<name, email, phone or company>  &page=<n>  → newest first, 25 a page, plus a
count per kind for the tabs.

``GET /quote-request/staff/<kind>/<id>/`` → everything the customer entered,
grouped into labelled sections.

"Handled" is the existing flag on each model: ``sent_quote`` (website form) and
``is_processed`` (container). Quotes are a few hundred rows, so the two tables
are filtered in the database and merged in Python.
"""

from django.db.models import Q, Value
from django.db.models.functions import Concat
from django.http import Http404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.container_conversion.models import ServiceRequest
from apps.core.permissions import IsDashboardAdmin

from .models import QuoteRequest, SolarQuoteDetails

SOLAR_SERVICE_TITLE = 'Solar PV System Design & Installation'
PAGE_SIZE = 25
KINDS = ('general', 'solar', 'container')
KIND_LABELS = {'general': 'General quote', 'solar': 'Solar PV quote', 'container': 'Container conversion'}

# Container add-ons, shown as one list instead of a Yes/No row each.
CONTAINER_ADDONS = [
    ('ablution_unit', 'Ablution unit'),
    ('electrical_installation', 'Electrical installation'),
    ('plumbing_installation', 'Plumbing installation'),
    ('insulation', 'Insulation'),
    ('interior_finishes', 'Interior finishes'),
    ('air_conditioning', 'Air conditioning'),
    ('solar_backup_power', 'Solar backup power'),
    ('custom_painting_branding', 'Custom painting & branding'),
    ('delivery_and_installation', 'Delivery & installation'),
]


def _quote_qs(kind, q, status):
    qs = QuoteRequest.objects.select_related('service')
    qs = qs.filter(service__title=SOLAR_SERVICE_TITLE) if kind == 'solar' else qs.exclude(service__title=SOLAR_SERVICE_TITLE)
    if status:
        qs = qs.filter(sent_quote=(status == 'handled'))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(company__icontains=q))
    return qs


def _container_qs(q, status):
    qs = ServiceRequest.objects.all()
    if status:
        qs = qs.filter(is_processed=(status == 'handled'))
    if q:
        qs = qs.annotate(full_name=Concat('first_name', Value(' '), 'last_name')).filter(
            Q(full_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(company_name__icontains=q)
        )
    return qs


def _quote_row(obj, kind):
    return {
        'key': f'{kind}-{obj.pk}',
        'kind': kind,
        'kind_label': KIND_LABELS[kind],
        'id': obj.pk,
        'name': obj.name,
        'company': obj.company or '',
        'email': obj.email,
        'phone': obj.phone,
        'summary': obj.service.title if obj.service else '',
        'created_at': obj.created_at,
        'handled': obj.sent_quote,
        'status_label': 'Quote sent' if obj.sent_quote else 'New',
    }


def _container_row(obj):
    return {
        'key': f'container-{obj.pk}',
        'kind': 'container',
        'kind_label': KIND_LABELS['container'],
        'id': obj.pk,
        'name': f'{obj.first_name} {obj.last_name}'.strip(),
        'company': obj.company_name or '',
        'email': obj.email,
        'phone': obj.phone,
        'summary': ' · '.join(filter(None, [obj.get_modular_size_display(), obj.get_intended_use_display()])),
        'created_at': obj.created_at,
        'handled': obj.is_processed,
        'status_label': 'Processed' if obj.is_processed else 'New',
    }


def _int(value, default=1):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


class StaffQuoteListView(APIView):
    permission_classes = [IsDashboardAdmin]

    def get(self, request):
        p = request.query_params
        kind = p.get('kind') if p.get('kind') in KINDS else ''
        status = p.get('status') if p.get('status') in ('new', 'handled') else ''
        q = (p.get('q') or '').strip()[:100]

        querysets = {
            'general': _quote_qs('general', q, status),
            'solar': _quote_qs('solar', q, status),
            'container': _container_qs(q, status),
        }
        counts = {k: qs.count() for k, qs in querysets.items()}
        counts['all'] = sum(counts.values())
        counts['new'] = (
            _quote_qs('general', q, 'new').count() + _quote_qs('solar', q, 'new').count() + _container_qs(q, 'new').count()
        )

        rows = []
        for k in ([kind] if kind else KINDS):
            if k == 'container':
                rows += [_container_row(o) for o in querysets[k]]
            else:
                rows += [_quote_row(o, k) for o in querysets[k]]
        rows.sort(key=lambda r: (r['created_at'], r['id']), reverse=True)

        total = len(rows)
        total_pages = max(1, -(-total // PAGE_SIZE))
        page = min(_int(p.get('page')), total_pages)
        start = (page - 1) * PAGE_SIZE
        return Response({
            'results': rows[start:start + PAGE_SIZE],
            'count': total,
            'page': page,
            'total_pages': total_pages,
            'counts': counts,
        })


def _label(value, choices):
    return dict(choices).get(value, value) if value else ''


def _section(title, rows):
    rows = [{'label': label, 'value': str(value)} for label, value in rows if value not in (None, '')]
    return {'title': title, 'rows': rows} if rows else None


def _quote_detail(obj, kind):
    sections = [_section('Contact', [
        ('Name', obj.name), ('Company', obj.company), ('Email', obj.email), ('Phone', obj.phone),
    ]), _section('Request', [('Service', obj.service.title if obj.service else '')])]
    solar = getattr(obj, 'solar_details', None) if kind == 'solar' else None
    if solar:
        d = SolarQuoteDetails
        sections.append(_section('Property', [
            ('Property type', _label(solar.property_type, d.PROPERTY_TYPE_CHOICES)),
            ('Suburb', solar.suburb),
            ('Province', _label(solar.province, d.PROVINCE_CHOICES)),
            ('Roof type', _label(solar.roof_type, d.ROOF_TYPE_CHOICES)),
            ('Grid connection', _label(solar.grid_connection, d.GRID_CONNECTION_CHOICES)),
        ]))
        sections.append(_section('Needs & budget', [
            ('Monthly electricity bill', f'R {solar.monthly_bill:,}'.replace(',', ' ') if solar.monthly_bill else ''),
            ('Primary goal', _label(solar.primary_goal, d.PRIMARY_GOAL_CHOICES)),
            ('Budget range', _label(solar.budget_range, d.BUDGET_RANGE_CHOICES)),
            ('Timeline', _label(solar.timeline, d.TIMELINE_CHOICES)),
            ('Heard about APS', _label(solar.referral_source, d.REFERRAL_SOURCE_CHOICES)),
        ]))
    notes = '\n\n'.join(filter(None, [obj.message or '', (solar.additional_notes or '') if solar else '']))
    return {**_quote_row(obj, kind), 'sections': [s for s in sections if s], 'message': notes}


def _container_detail(obj):
    addons = [label for field, label in CONTAINER_ADDONS if getattr(obj, field, False)]
    sections = [
        _section('Contact', [
            ('Name', f'{obj.first_name} {obj.last_name}'.strip()), ('Company', obj.company_name),
            ('Email', obj.email), ('Phone', obj.phone),
            ('Prefers', obj.get_preferred_contact_method_display()),
        ]),
        _section('Unit', [
            ('Unit type', obj.get_unit_type_display()),
            ('Intended use', obj.get_intended_use_display()),
            ('Size', obj.get_modular_size_display()),
            ('Finish level', obj.get_finish_level_display()),
            ('Add-ons', ', '.join(addons) or 'None'),
        ]),
        _section('Project', [
            ('Timeframe', obj.get_project_timeframe_display()),
            ('Budget range', obj.get_budget_range_display()),
            ('Delivery / export address', obj.transport_or_export_address),
        ]),
        _section('APS notes', [('Notes', obj.notes)]),
    ]
    return {**_container_row(obj), 'sections': [s for s in sections if s], 'message': obj.additional_details or ''}


class StaffQuoteDetailView(APIView):
    permission_classes = [IsDashboardAdmin]

    def get(self, request, kind, pk):
        if kind == 'container':
            obj = ServiceRequest.objects.filter(pk=pk).first()
            if obj is None:
                raise Http404
            return Response(_container_detail(obj))
        if kind not in ('general', 'solar'):
            raise Http404
        obj = _quote_qs(kind, '', '').select_related('solar_details').filter(pk=pk).first()
        if obj is None:
            raise Http404
        return Response(_quote_detail(obj, kind))
