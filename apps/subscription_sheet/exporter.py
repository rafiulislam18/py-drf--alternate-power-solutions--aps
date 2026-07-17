"""
Sync valid subscriptions from both apps into the "APS Subscriptions" Google Sheet.

Sources:
  - apps.subscription.Subscription           -> "Inverter & Battery Monitoring Plan" (R99/mo)
  - apps.request_solar_cleaning.Subscription -> "Solar & Inverter Maintenance Plan" (R199/mo)

VALID = has a non-empty payfast_token (a real, paid-up recurring subscription).
Only these are exported.

The sheet UPSERTS by a composite key "<app>:<id>": new subscriptions are
appended, existing rows have their data/date columns refreshed (so payment
dates stay current) while the admin-edited Comments column is never touched.

Excluded from the sheet (per ops request): is_active, payfast_token,
payfast_payment_id, internal id.
"""

import logging

import requests
from django.conf import settings
from django.utils import timezone

from apps.request_solar_cleaning.models import Subscription as CleaningSubscription
from apps.subscription.models import Subscription as InverterSubscription
from .models import ExportedSubscription

logger = logging.getLogger(__name__)

INVERTER_PLAN = 'Inverter & Battery Monitoring Plan'
CLEANING_PLAN = 'Solar & Inverter Maintenance Plan'


class SubscriptionSheetConfigError(Exception):
    """Raised when the sheet URL/token isn't configured."""


def _fmt_dt(dt):
    """Format a stored (UTC) datetime to Cape Town date-time for the sheet."""
    if not dt:
        return ''
    from zoneinfo import ZoneInfo
    tz = getattr(settings, 'WHATSAPP_EXPORT_TIMEZONE', None) or 'Africa/Johannesburg'
    return timezone.localtime(dt, ZoneInfo(tz)).strftime('%d %b %Y %H:%M')


def _client_bits(sub):
    """(name, email, phone) from the subscription's client, blank-safe."""
    c = sub.client
    if not c:
        return '', '', ''
    return c.name or '', c.email or '', c.phone or ''


def _valid_qs(model):
    """
    Valid subscriptions to export: must have a real PayFast token AND be active.
    Both conditions required — a tokenless or inactive subscription is not synced.

    Note: is_active is used only as a FILTER here; it is deliberately NOT shown as
    a sheet column (per ops request, since the flag can read 'active' even when a
    client hasn't paid for months).
    """
    return (model.objects
            .filter(is_active=True)
            .exclude(payfast_token__isnull=True)
            .exclude(payfast_token__exact='')
            .select_related('client'))


def _build_items():
    """Build the list of subscription dicts to send (both apps)."""
    items = []

    for sub in _valid_qs(InverterSubscription):
        name, email, phone = _client_bits(sub)
        items.append({
            'key': f"{ExportedSubscription.APP_MONITORING}:{sub.id}",
            'app_label': ExportedSubscription.APP_MONITORING,
            'subscription_id': sub.id,
            'plan': INVERTER_PLAN,
            'client_name': name, 'client_email': email, 'client_phone': phone,
            'address': sub.address or '',
            'inverter_type': sub.inverter_type or '',
            'inverter_size': '',            # not on the inverter plan
            'installed_panels': '',         # not on the inverter plan
            'subscription_length': sub.subscription_length,
            'call_out_balance': sub.call_out_balance,
            'last_payment_date': _fmt_dt(sub.last_payment_date),
            'updated_at': _fmt_dt(sub.updated_at),
            'created_at': _fmt_dt(sub.created_at),
        })

    for sub in _valid_qs(CleaningSubscription):
        name, email, phone = _client_bits(sub)
        items.append({
            'key': f"{ExportedSubscription.APP_MAINTENANCE}:{sub.id}",
            'app_label': ExportedSubscription.APP_MAINTENANCE,
            'subscription_id': sub.id,
            'plan': CLEANING_PLAN,
            'client_name': name, 'client_email': email, 'client_phone': phone,
            'address': sub.address or '',
            'inverter_type': sub.inverter_type or '',
            'inverter_size': sub.inverter_size or '',
            'installed_panels': sub.installed_panels_count or '',
            'subscription_length': sub.subscription_length,
            'call_out_balance': None,       # not on the cleaning plan
            'last_payment_date': _fmt_dt(sub.last_payment_date),
            'updated_at': _fmt_dt(sub.updated_at),
            'created_at': _fmt_dt(sub.created_at),
        })

    return items


def sync_subscriptions():
    """
    Push all valid subscriptions to the APS Subscriptions sheet (upsert).

    Returns stats: {sent, created, updated, error}. Records/refreshes an
    ExportedSubscription row per sent item on success so we can report new vs.
    existing. Never raises for a delivery problem; raises
    SubscriptionSheetConfigError only for missing config.
    """
    url = settings.APS_SUBSCRIPTION_SHEET_URL
    token = settings.APS_SUBSCRIPTION_SHEET_TOKEN
    if not url or not token:
        raise SubscriptionSheetConfigError(
            "APS_SUBSCRIPTION_SHEET_URL / APS_SUBSCRIPTION_SHEET_TOKEN not configured."
        )

    items = _build_items()
    stats = {'sent': len(items), 'created': 0, 'updated': 0, 'error': None}
    if not items:
        return stats

    payload = {'token': token, 'type': 'subscriptions', 'subscriptions': items}

    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Subscription sheet sync failed: %s", exc)
        stats['error'] = str(exc)
        return stats

    if not data.get('ok'):
        stats['error'] = data.get('error', 'unknown error from sheet')
        logger.error("Subscription sheet rejected sync: %s", stats['error'])
        return stats

    stats['created'] = int(data.get('created', 0))
    stats['updated'] = int(data.get('updated', 0))

    # Record dedup rows for everything we successfully sent.
    for item in items:
        ExportedSubscription.objects.update_or_create(
            app_label=item['app_label'],
            subscription_id=item['subscription_id'],
        )

    logger.info("Subscription sheet sync: %s", stats)
    return stats
