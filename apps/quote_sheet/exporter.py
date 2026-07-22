"""
Sync all quote requests into two tabs of the "APS Open Jobs" Google Sheet.

Sources:
  - apps.quote_request.QuoteRequest         -> "Quote Requests" tab
      (+ its optional one-to-one SolarQuoteDetails, flattened into extra columns)
  - apps.container_conversion.ServiceRequest -> "Container Conversion Quotes" tab

Per ops request we export ALL rows (regardless of status). Each sheet UPSERTS by
the row's database id: new quotes are appended, existing rows have their columns
refreshed, and any admin-added "Notes" column in the sheet is never touched.

Routed to the same Apps Script web app as the jobs/subscriptions exports via a
`type` field in the POST ("quote_requests" and "container_quotes").
"""

import logging

import requests
from django.conf import settings
from django.utils import timezone

from apps.quote_request.models import QuoteRequest
from apps.container_conversion.models import ServiceRequest
from .models import ExportedQuote

logger = logging.getLogger(__name__)


class QuoteSheetConfigError(Exception):
    """Raised when the sheet URL/token isn't configured."""


def _fmt_dt(dt):
    """Format a stored (UTC) datetime to Cape Town date-time for the sheet."""
    if not dt:
        return ''
    from zoneinfo import ZoneInfo
    tz = getattr(settings, 'WHATSAPP_EXPORT_TIMEZONE', None) or 'Africa/Johannesburg'
    return timezone.localtime(dt, ZoneInfo(tz)).strftime('%d %b %Y %H:%M')


def _yes_no(val):
    return 'Yes' if val else 'No'


def _build_regular_items():
    """One dict per QuoteRequest, with SolarQuoteDetails flattened in."""
    items = []
    qs = (QuoteRequest.objects
          .select_related('service', 'solar_details')
          .order_by('id'))

    for q in qs:
        d = getattr(q, 'solar_details', None)
        items.append({
            'key': f"{ExportedQuote.KIND_REGULAR}:{q.id}",
            'quote_id': q.id,
            'name': q.name or '',
            'email': q.email or '',
            'phone': q.phone or '',
            'company': q.company or '',
            'service': q.service.title if q.service else '',
            'message': q.message or '',
            'sent_quote': _yes_no(q.sent_quote),
            # --- Solar-specific details (blank when not a solar request) ---
            'property_type': d.get_property_type_display() if d and d.property_type else '',
            'suburb': (d.suburb or '') if d else '',
            'province': d.get_province_display() if d and d.province else '',
            'roof_type': d.get_roof_type_display() if d and d.roof_type else '',
            'monthly_bill': (d.monthly_bill if d and d.monthly_bill is not None else ''),
            'primary_goal': d.get_primary_goal_display() if d and d.primary_goal else '',
            'grid_connection': d.get_grid_connection_display() if d and d.grid_connection else '',
            'budget_range': d.get_budget_range_display() if d and d.budget_range else '',
            'timeline': d.get_timeline_display() if d and d.timeline else '',
            'referral_source': d.get_referral_source_display() if d and d.referral_source else '',
            'additional_notes': (d.additional_notes or '') if d else '',
            'created_at': _fmt_dt(q.created_at),
        })
    return items


def _build_container_items():
    """One dict per container-conversion ServiceRequest (all fields)."""
    items = []
    for r in ServiceRequest.objects.order_by('id'):
        items.append({
            'key': f"{ExportedQuote.KIND_CONTAINER}:{r.id}",
            'quote_id': r.id,
            # ---- Contact ----
            'first_name': r.first_name or '',
            'last_name': r.last_name or '',
            'email': r.email or '',
            'phone': r.phone or '',
            'preferred_contact_method': r.get_preferred_contact_method_display(),
            'company_name': r.company_name or '',
            # ---- Unit configuration ----
            'unit_type': r.get_unit_type_display(),
            'intended_use': r.get_intended_use_display(),
            'modular_size': r.get_modular_size_display(),
            # ---- Optional extras ----
            'ablution_unit': _yes_no(r.ablution_unit),
            'electrical_installation': _yes_no(r.electrical_installation),
            'plumbing_installation': _yes_no(r.plumbing_installation),
            'insulation': _yes_no(r.insulation),
            'interior_finishes': _yes_no(r.interior_finishes),
            'air_conditioning': _yes_no(r.air_conditioning),
            'solar_backup_power': _yes_no(r.solar_backup_power),
            'custom_painting_branding': _yes_no(r.custom_painting_branding),
            'delivery_and_installation': _yes_no(r.delivery_and_installation),
            # ---- Project details ----
            'project_timeframe': r.get_project_timeframe_display() if r.project_timeframe else '',
            'budget_range': r.get_budget_range_display(),
            'finish_level': r.get_finish_level_display(),
            # ---- Delivery & other ----
            'transport_or_export_address': r.transport_or_export_address or '',
            'additional_details': r.additional_details or '',
            # ---- Admin ----
            'is_processed': _yes_no(r.is_processed),
            'created_at': _fmt_dt(r.created_at),
            'updated_at': _fmt_dt(r.updated_at),
        })
    return items


def _post(url, payload):
    """POST to the sheet web app; return (ok, data_or_error)."""
    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — delivery problems are reported, not raised
        return False, str(exc)
    if not data.get('ok'):
        return False, data.get('error', 'unknown error from sheet')
    return True, data


def _sync_one(kind, sheet_type, items):
    """
    Push one kind's items to its tab (upsert) and record dedup rows on success.

    Returns {sent, created, updated, error}.
    """
    url = settings.APS_QUOTE_SHEET_URL
    token = settings.APS_QUOTE_SHEET_TOKEN
    if not url or not token:
        raise QuoteSheetConfigError(
            "APS_QUOTE_SHEET_URL / APS_QUOTE_SHEET_TOKEN not configured."
        )

    stats = {'sent': len(items), 'created': 0, 'updated': 0, 'error': None}
    if not items:
        return stats

    payload = {'token': token, 'type': sheet_type, 'quotes': items}
    ok, data = _post(url, payload)
    if not ok:
        logger.error("Quote sheet sync (%s) failed: %s", kind, data)
        stats['error'] = data
        return stats

    stats['created'] = int(data.get('created', 0))
    stats['updated'] = int(data.get('updated', 0))

    for item in items:
        ExportedQuote.objects.update_or_create(kind=kind, quote_id=item['quote_id'])

    logger.info("Quote sheet sync (%s): %s", kind, stats)
    return stats


def sync_quotes():
    """
    Push all quote requests to their two sheet tabs (upsert).

    Returns {regular: {...}, container: {...}}. Never raises for a delivery
    problem (recorded per-kind in 'error'); raises QuoteSheetConfigError only
    for missing config.
    """
    return {
        'regular': _sync_one(
            ExportedQuote.KIND_REGULAR, 'quote_requests', _build_regular_items()
        ),
        'container': _sync_one(
            ExportedQuote.KIND_CONTAINER, 'container_quotes', _build_container_items()
        ),
    }
