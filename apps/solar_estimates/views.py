"""
Solar estimates API (staff only), mounted at /solar-estimates/. The document is
the "Solar & Battery Backup – Proposal & Quotation".

    POST  preview/                 {inputs, edits, estimate_id?} → {html, editables, summary, warnings} (nothing saved)
    GET   /  ?q=&status=&page=     saved proposals, newest first
    POST  /                        {inputs, edits} → new draft
    GET   <id>/                    the proposal (+ its HTML once confirmed)
    PATCH <id>/                    {inputs, edits} (drafts only)
    DELETE <id>/
    POST  <id>/confirm/            {inputs?, edits?} → freezes the document, gives it its quotation number
    POST  <id>/reopen/             back to draft (to change a confirmed proposal)
    GET   <id>/pdf/                the confirmed proposal as a PDF
    POST/DELETE <id>/roof-image/   the roof layout picture (drafts only)
    GET/PUT settings/              assumptions & company details (+ the catalogue on GET)
    POST  catalogue/  PATCH/DELETE catalogue/<id>/

Prices are kept here, in Prices & settings. A fresh server's catalogue is loaded
once from the workbook with ``manage.py import_estimate_template <file.xlsx>``.
"""

import io
import re
import uuid
from functools import partial

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsDashboardAdmin
from utils.exceptions import custom_exception_handler

from . import document, engine
from .models import CatalogueItem, EstimateSettings, SolarEstimate
from .numbering import next_quote_number, suggest_site_code
from .pdf import PdfUnavailable, html_to_pdf
from .serializers import (
    CatalogueItemSerializer,
    EditsField,
    EstimateInputSerializer,
    EstimateListSerializer,
    SettingsSerializer,
)

PAGE_SIZE = 25
COMPANY_FIELDS = ['company_vat_number', 'company_phone', 'company_address', 'company_email', 'default_prepared_by',
                  'validity_days', 'deposit_percent', 'balance_terms', 'quote_prefix', 'quote_counter_reset']
ROOF_MAX_BYTES = 10 * 1024 * 1024
ROOF_MAX_SIDE = 1800  # px — plenty for a half-page picture, keeps the PDF small
ROOF_MAX_PIXELS = 40_000_000  # refuse before decoding (memory); a roof screenshot is a few megapixels


class _StaffView(APIView):
    permission_classes = [IsDashboardAdmin]

    def get_exception_handler(self):
        return partial(custom_exception_handler, keep_fields=True)


def _settings_dict(obj=None):
    obj = obj or EstimateSettings.load()
    return {**obj.as_engine_dict(), **{f: getattr(obj, f) for f in COMPANY_FIELDS}}


def _catalogue():
    return [c.as_engine_dict() for c in CatalogueItem.objects.all()]


def _validated(data):
    ser = EstimateInputSerializer(data=data.get('inputs') or {})
    ser.is_valid(raise_exception=True)
    edits = EditsField().run_validation(data.get('edits') or {})
    return ser.to_engine(), edits


def _quote_date(inputs):
    from datetime import date
    try:
        return date.fromisoformat(inputs['estimate_date']) if inputs.get('estimate_date') else timezone.localdate()
    except ValueError:
        return timezone.localdate()


def proposed_number(inputs, s, exclude_pk=None):
    """The quotation number this proposal would get now (typed number wins)."""
    if (inputs.get('estimate_number') or '').strip():
        return inputs['estimate_number'].strip()
    used = SolarEstimate.objects.exclude(number='')
    if exclude_pk:
        used = used.exclude(pk=exclude_pk)
    site = inputs.get('site_code') or suggest_site_code(inputs.get('site_address'))
    return next_quote_number(s['quote_prefix'], _quote_date(inputs), site, s['quote_counter_reset'],
                             used.values_list('number', flat=True))


def compute(inputs, edits, quote_number=''):
    s = _settings_dict()
    result = engine.calculate(inputs, s, _catalogue())
    doc = document.build(inputs, result, s, edits, quote_number=quote_number)
    return result, doc


def _warnings(inputs, result):
    out = []
    usage = result['usage']
    if usage['status_level'] == 'error' and not (inputs.get('overrides') or {}).get('daily_kwh'):
        out.append({'level': 'error', 'text': f"{usage['status']} — or enter the daily consumption as an override."})
    for o in result['options']:
        name = f"{o['tier']} {'Smart split' if o['scope'] == 'split' else 'Full cut-over'}"
        if not o['inverter']:
            out.append({'level': 'warning', 'text': f'{name}: no single inverter fits — parallel units or a larger '
                                                    'model needed (check the catalogue or the inverter overrides).'})
        if not o['battery']:
            out.append({'level': 'warning', 'text': f"{name}: there is no {o['tier']} battery in the catalogue."})
        elif not o['battery']['usable_each']:
            out.append({'level': 'warning', 'text': f"{name}: the battery “{o['battery']['model']}” has no usable kWh "
                                                    'in Prices & settings, so the battery can’t be sized.'})
        if o['unpriced']:
            out.append({'level': 'warning', 'text': f"{name}: {o['unpriced']} line(s) have no price in the catalogue, "
                                                    "so it shows “Price TBC”."})
    return out


def _summary(result):
    s = result['sizing']
    return {
        'usage': {k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in result['usage'].items()},
        'sizing': {k: s[k] for k in ('daily', 'optimal_panels', 'panels', 'kwp', 'annual_generation',
                                     'share_from_solar', 'current_annual_cost', 'saving_split', 'saving_full',
                                     'battery_split', 'battery_full', 'split_min_kw', 'full_min_kw', 'phase',
                                     'lead_scope')},
        'options': [{k: o[k] for k in ('key', 'tier', 'scope', 'recommended', 'inverter', 'battery', 'subtotal',
                                       'total', 'saving_client', 'payback_years', 'payback_simple',
                                       'ten_year_saving', 'net_10y',
                                       'first_year_return', 'unpriced', 'lines', 'cost_excl_vat')}
                    for o in result['options']],
    }


def _roof_url(request, est):
    return request.build_absolute_uri(est.roof_image.url) if est and est.roof_image else ''


class PreviewView(_StaffView):
    def post(self, request):
        inputs, edits = _validated(request.data)
        est = None
        if request.data.get('estimate_id'):
            est = SolarEstimate.objects.filter(pk=request.data['estimate_id']).first()
        number = proposed_number(inputs, _settings_dict(), exclude_pk=est.pk if est else None)
        result, doc = compute(inputs, edits, quote_number=number)
        return Response({
            'html': document.render_html(doc, editable=True, roof_image=_roof_url(request, est)),
            'editables': document.editables(doc, result),
            'summary': _summary(result),
            'warnings': _warnings(inputs, result),
            'quote_number': number,
            'site_code': inputs.get('site_code') or suggest_site_code(inputs.get('site_address')),
        })


def _detail(request, est):
    data = {
        **EstimateListSerializer(est).data,
        'inputs': est.inputs, 'edits': est.edits,
        'roof_image_url': _roof_url(request, est),
    }
    if est.status == SolarEstimate.Status.ISSUED and est.snapshot:
        data['html'] = document.render_html(est.snapshot, editable=False, roof_image=_roof_url(request, est))
    return data


def _recommended_total(doc):
    lead = next((o for o in doc['options'] if o.get('recommended') or '(recommended)' in o.get('heading', '')), None)
    return round(lead['total_value'], 2) if lead and lead.get('total_value') is not None else None


def _store(est, inputs, edits, doc):
    est.inputs, est.edits = inputs, edits
    est.client_name = (inputs.get('client_name') or '')[:200]
    est.site_address = (inputs.get('site_address') or '')[:300]
    est.total_incl_vat = _recommended_total(doc)


class EstimateListView(_StaffView):
    def get(self, request):
        qs = SolarEstimate.objects.select_related('created_by')
        q = (request.query_params.get('q') or '').strip()[:100]
        if q:
            qs = qs.filter(Q(number__icontains=q) | Q(client_name__icontains=q) | Q(site_address__icontains=q))
        if request.query_params.get('status') in ('draft', 'issued'):
            qs = qs.filter(status=request.query_params['status'])
        total = qs.count()
        pages = max(1, -(-total // PAGE_SIZE))
        try:
            page = min(max(1, int(request.query_params.get('page', 1))), pages)
        except ValueError:
            page = 1
        rows = qs[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        return Response({'results': EstimateListSerializer(rows, many=True).data, 'count': total,
                         'page': page, 'total_pages': pages})

    def post(self, request):
        inputs, edits = _validated(request.data)
        _, doc = compute(inputs, edits)
        est = SolarEstimate(created_by=request.user)
        _store(est, inputs, edits, doc)
        est.save()
        return Response(_detail(request, est), status=status.HTTP_201_CREATED)


def _draft_or_409(est):
    if est.status == SolarEstimate.Status.ISSUED:
        return Response({'detail': 'This proposal is confirmed. Reopen it to make changes.'},
                        status=status.HTTP_409_CONFLICT)
    return None


class EstimateDetailView(_StaffView):
    def get(self, request, pk):
        return Response(_detail(request, get_object_or_404(SolarEstimate, pk=pk)))

    def patch(self, request, pk):
        est = get_object_or_404(SolarEstimate, pk=pk)
        if (blocked := _draft_or_409(est)):
            return blocked
        inputs, edits = _validated(request.data)
        _, doc = compute(inputs, edits)
        _store(est, inputs, edits, doc)
        est.save()
        return Response(_detail(request, est))

    def delete(self, request, pk):
        est = get_object_or_404(SolarEstimate, pk=pk)
        if est.roof_image:
            est.roof_image.delete(save=False)
        est.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EstimateConfirmView(_StaffView):
    def post(self, request, pk):
        for attempt in range(3):  # a number taken at the same moment by someone else → take the next one
            try:
                with transaction.atomic():
                    return self._confirm(request, pk)
            except IntegrityError:
                if attempt == 2:
                    raise
        return None

    def _confirm(self, request, pk):
        est = get_object_or_404(SolarEstimate.objects.select_for_update(), pk=pk)
        if est.status == SolarEstimate.Status.ISSUED:
            return Response(_detail(request, est))
        if 'inputs' in request.data:
            inputs, edits = _validated(request.data)
        else:
            inputs, edits = _validated({'inputs': est.inputs, 'edits': est.edits})
        if not inputs.get('estimate_date'):
            inputs['estimate_date'] = timezone.localdate().isoformat()
        s = _settings_dict()
        typed = (edits.get('quote_no') or inputs.get('estimate_number') or '').strip()
        number = typed or proposed_number(inputs, s, exclude_pk=est.pk)
        if SolarEstimate.objects.filter(number=number).exclude(pk=est.pk).exists():
            return Response({'detail': f'Quotation number {number} is already used — choose another.'},
                            status=status.HTTP_400_BAD_REQUEST)
        result, doc = compute(inputs, edits, quote_number=number)
        blocking = [w for w in _warnings(inputs, result) if w['level'] == 'error']
        if blocking:
            return Response({'detail': blocking[0]['text']}, status=status.HTTP_400_BAD_REQUEST)
        _store(est, inputs, edits, doc)
        est.number = number[:40]
        est.snapshot = doc
        est.status = SolarEstimate.Status.ISSUED
        est.issued_at = timezone.now()
        est.save()
        return Response(_detail(request, est))


class EstimateReopenView(_StaffView):
    def post(self, request, pk):
        est = get_object_or_404(SolarEstimate, pk=pk)
        est.status, est.snapshot, est.issued_at = SolarEstimate.Status.DRAFT, None, None
        est.save(update_fields=['status', 'snapshot', 'issued_at', 'updated_at'])
        return Response(_detail(request, est))


def _safe_filename(text):
    return re.sub(r'[^\w\- ,.()&]+', '', text).strip()[:150] or 'proposal'


def pdf_filename(est):
    """APS-PRJ-2610-CHC-001 22 Chateau Circle Solar & Battery Backup.pdf"""
    site = (est.site_address or est.client_name).split(',')[0]
    return _safe_filename(f'{est.number} {site} Solar & Battery Backup') + '.pdf'


class EstimatePdfView(_StaffView):
    def get(self, request, pk):
        est = get_object_or_404(SolarEstimate, pk=pk)
        if est.status != SolarEstimate.Status.ISSUED or not est.snapshot:
            return Response({'detail': 'Confirm the proposal first — the PDF is made from the confirmed version.'},
                            status=status.HTTP_409_CONFLICT)
        try:
            html = document.render_html(est.snapshot, editable=False, roof_image=document.image_data_uri(est.roof_image))
            pdf = html_to_pdf(html)
        except PdfUnavailable as exc:
            return Response({'detail': f'The PDF could not be made on the server ({exc}).'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{pdf_filename(est)}"'
        return response


class RoofImageView(_StaffView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        est = get_object_or_404(SolarEstimate, pk=pk)
        if (blocked := _draft_or_409(est)):
            return blocked
        upload = request.FILES.get('file')
        if not upload:
            return Response({'file': ['Choose the roof layout picture (JPG or PNG).']}, status=status.HTTP_400_BAD_REQUEST)
        if upload.size > ROOF_MAX_BYTES:
            return Response({'file': ['The picture must be 10 MB or smaller.']}, status=status.HTTP_400_BAD_REQUEST)
        try:
            img = Image.open(upload)  # reads the header only
            if img.width * img.height > ROOF_MAX_PIXELS:  # a small file can still decode to gigabytes
                return Response({'file': ['That picture is too large — use a screenshot under 40 megapixels.']},
                                status=status.HTTP_400_BAD_REQUEST)
            img.load()
        except Image.DecompressionBombError:
            return Response({'file': ['That picture is too large — use a screenshot under 40 megapixels.']},
                            status=status.HTTP_400_BAD_REQUEST)
        except (UnidentifiedImageError, OSError, ValueError):
            return Response({'file': ['That file is not a picture (use JPG or PNG).']}, status=status.HTTP_400_BAD_REQUEST)
        img.thumbnail((ROOF_MAX_SIDE, ROOF_MAX_SIDE))
        out = io.BytesIO()
        img.convert('RGB').save(out, format='JPEG', quality=85, optimize=True)
        if est.roof_image:
            est.roof_image.delete(save=False)
        est.roof_image.save(f'{uuid.uuid4().hex}.jpg', ContentFile(out.getvalue()), save=True)
        return Response(_detail(request, est))

    def delete(self, request, pk):
        est = get_object_or_404(SolarEstimate, pk=pk)
        if (blocked := _draft_or_409(est)):
            return blocked
        if est.roof_image:
            est.roof_image.delete(save=True)
        return Response(_detail(request, est))


class SettingsView(_StaffView):
    def get(self, request):
        obj = EstimateSettings.load()
        return Response({
            'settings': SettingsSerializer(obj).data,
            'catalogue': CatalogueItemSerializer(CatalogueItem.objects.all(), many=True,
                                                 context={'margin': obj.target_margin,
                                                          'rounding': obj.price_rounding}).data,
        })

    def put(self, request):
        ser = SettingsSerializer(EstimateSettings.load(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return self.get(request)


class CatalogueCreateView(_StaffView):
    def post(self, request):
        ser = CatalogueItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if 'sort_order' not in request.data:
            # New rows go to the end: the engine uses the first matching battery / panel, so a blank
            # new row must never jump ahead of the one in use.
            last = CatalogueItem.objects.order_by('-sort_order').values_list('sort_order', flat=True).first()
            ser.save(sort_order=(last or 0) + 1)
        else:
            ser.save()
        return Response(ser.data, status=status.HTTP_201_CREATED)


class CatalogueDetailView(_StaffView):
    def patch(self, request, pk):
        ser = CatalogueItemSerializer(get_object_or_404(CatalogueItem, pk=pk), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    def delete(self, request, pk):
        get_object_or_404(CatalogueItem, pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

