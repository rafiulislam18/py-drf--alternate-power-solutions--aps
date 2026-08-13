"""
Hourly low-gas alerting.

Every hour (scheduled via django-celery-beat) we check every active site's
remaining gas percentage. When a cylinder crosses at/below the low threshold
(default 30%) we email both the client (site owner) and the APS admin inbox.

Alerting is edge-triggered per site via LowGasAlertState: an alert fires once
when a site drops to low, stays quiet while it remains low, and a recovery email
is sent once when a refill lifts it back above the threshold. This is what stops
the hourly task from re-sending the same alert every run.
"""

import logging
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.gas_guard.weight_scale.models import ScaleDevice
from apps.gas_guard.weight_scale.services import site_payload

from .emails import send_low_gas_email

logger = logging.getLogger(__name__)


def _schedule_now():
    """Current time in the client timezone (SAST), for human-readable stamps."""
    tz_name = getattr(settings, 'CELERY_TIMEZONE', None) or 'UTC'
    return timezone.now().astimezone(ZoneInfo(tz_name))


def _pct_from_payload(payload):
    """Remaining gas percentage (0–100 int) from a site_payload dict."""
    gas_kg = max(payload['latest']['totalKg'] - payload['tareKg'], 0.0)
    full = payload['fullGasKg'] or 0
    if full <= 0:
        return 0
    return round((gas_kg / full) * 100)


def _recipients_for(device):
    """Client (owner) + admin addresses for this device's alerts."""
    recipients = []
    owner = device.owner
    if owner is not None:
        # Prefer the owner's chosen notify address, else their login email.
        recipients.append(getattr(owner, 'notify_email', '') or owner.email)
    admin = getattr(settings, 'EMAIL_RECIPIENT', None)
    if admin:
        recipients.append(admin)
    return recipients


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def check_low_gas(self):
    """
    Check every active site's gas level and fire edge-triggered low-gas alerts.
    Runs hourly via Celery Beat.
    """
    threshold = settings.GAS_GUARD_LOW_GAS_THRESHOLD_PCT
    now = timezone.now()
    checked_at = _schedule_now().strftime('%d/%m/%Y %H:%M:%S')

    devices = ScaleDevice.objects.filter(is_active=True).select_related('owner')
    low_count = 0
    alerted = 0
    recovered = 0

    for device in devices:
        # No readings yet → can't judge; skip (a fresh device isn't "low").
        if device.readings.first() is None:
            continue

        payload = site_payload(device, include_estimate=True, now=now)
        pct = _pct_from_payload(payload)
        is_low = pct <= threshold
        if is_low:
            low_count += 1

        context = {
            'site_name': payload['name'],
            'pct': pct,
            'gas_kg': max(payload['latest']['totalKg'] - payload['tareKg'], 0.0),
            'threshold': threshold,
            'est_days': payload['estDaysLeft'],
            'checked_at': checked_at,
        }

        outcome = _handle_transition(device, is_low, context, now)
        if outcome == 'alerted':
            alerted += 1
        elif outcome == 'recovered':
            recovered += 1

    msg = (
        f'Low-gas check done: {devices.count()} site(s), {low_count} low, '
        f'{alerted} new alert(s), {recovered} recovery notice(s).'
    )
    logger.info(msg)
    return msg


def _handle_transition(device, is_low, context, now):
    """
    Compare the site's current low/ok state with the stored latch and email only
    on a transition (ok → low, or low → ok). Returns 'alerted', 'recovered', or
    None. Email failures never propagate.
    """
    from .models import LowGasAlertState

    state, _ = LowGasAlertState.objects.get_or_create(device=device)
    recipients = _recipients_for(device)

    if is_low and not state.is_active:
        # Rising edge: cylinder just went low.
        state.is_active = True
        state.last_pct = context['pct']
        state.last_triggered_at = now
        state.save()
        context = {**context, 'is_fault': True}
        sent = send_low_gas_email(context, recipients)
        logger.warning(
            'Low-gas alert for %s at %s%% (email sent=%s)',
            device, context['pct'], sent,
        )
        return 'alerted'

    if not is_low and state.is_active:
        # Falling edge: cylinder refilled above threshold.
        state.is_active = False
        state.last_pct = context['pct']
        state.last_recovered_at = now
        state.save()
        context = {**context, 'is_fault': False}
        sent = send_low_gas_email(context, recipients)
        logger.info(
            'Low-gas recovery for %s at %s%% (email sent=%s)',
            device, context['pct'], sent,
        )
        return 'recovered'

    # No transition — keep the latest pct but stay quiet.
    state.last_pct = context['pct']
    state.save(update_fields=['last_pct', 'updated_at'])
    return None
