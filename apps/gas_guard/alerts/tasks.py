"""
Hourly low-gas alerting.

Every hour (scheduled via django-celery-beat) we check every active site's
remaining gas percentage. When a cylinder crosses at/below the low threshold
(default 30%) we email the APS admin inbox and push to the Gas Guard admins'
devices (apps.push.gas; no push on a refill), for every site.

The site's owner gets the email (and the push) only while THAT site has an
active Monitoring subscription (``site_is_monitored``): low-gas alerts are part
of the paid plan, and billing is per site. The email also needs the owner's
"Low-gas email alerts" switch on (Settings; push has its own settings). "Active" = its Subscription row has
``is_active`` (set by a COMPLETE PayFast payment, cleared straight away by a
CANCELLED one or by cancelling in Plans; a FAILED payment changes nothing).
The same goes for the refill email.

Alerting is edge-triggered per site via LowGasAlertState: an alert fires once
when a site drops to low, stays quiet while it remains low, and a recovery email
is sent once when a refill lifts it back above the threshold. This is what stops
the hourly task from re-sending the same alert every run.

A second "Gas almost empty" alert fires the same way at the critical threshold
(default 10%), to the same people. A drop straight past both levels between two
checks sends only that one. A refill above the low threshold resets both (one
refill email); a refill that only gets back above the critical threshold
quietly re-arms that level.
"""

import logging
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.gas_guard.subscription.models import site_is_monitored
from apps.gas_guard.weight_scale.models import ScaleDevice
from apps.gas_guard.weight_scale.services import site_payload
from apps.push.gas import queue_low_gas_push

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
    """Admin inbox always; the owner only while the site is on the Monitoring plan
    and their "Low-gas email alerts" switch is on."""
    recipients = []
    owner = device.owner
    if owner is not None and owner.email_alerts_enabled and site_is_monitored(device):
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
    critical = settings.GAS_GUARD_CRITICAL_GAS_THRESHOLD_PCT
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
            'critical_threshold': critical,
            'est_days': payload['estDaysLeft'],
            'checked_at': checked_at,
        }

        outcome = _handle_transition(device, is_low, pct <= critical, context, now)
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


def _handle_transition(device, is_low, is_critical, context, now):
    """
    Compare the site's current ok/low/critical state with the stored latches and
    email only on a transition (→ critical, ok → low, or low → ok). Returns
    'alerted', 'recovered', or None. Email failures never propagate.
    """
    from .models import LowGasAlertState

    state, _ = LowGasAlertState.objects.get_or_create(device=device)
    recipients = _recipients_for(device)

    if is_critical and not state.is_critical:
        # Just went almost empty. Also latches low, so a drop straight past
        # both levels sends only this alert.
        state.is_active = True
        state.is_critical = True
        state.last_pct = context['pct']
        state.last_triggered_at = now
        state.save()
        context = {**context, 'is_fault': True, 'is_critical': True, 'threshold': context['critical_threshold']}
        sent = send_low_gas_email(context, recipients)
        queue_low_gas_push(device, context['site_name'], context['pct'],
                           critical=True, est_days=context['est_days'])
        logger.warning(
            'Gas almost empty alert for %s at %s%% (email sent=%s)',
            device, context['pct'], sent,
        )
        return 'alerted'

    if is_low and not state.is_active:
        # Rising edge: cylinder just went low.
        state.is_active = True
        state.last_pct = context['pct']
        state.last_triggered_at = now
        state.save()
        context = {**context, 'is_fault': True}
        sent = send_low_gas_email(context, recipients)
        queue_low_gas_push(device, context['site_name'], context['pct'])
        logger.warning(
            'Low-gas alert for %s at %s%% (email sent=%s)',
            device, context['pct'], sent,
        )
        return 'alerted'

    if not is_low and state.is_active:
        # Falling edge: cylinder refilled above threshold. Resets both levels.
        state.is_active = False
        state.is_critical = False
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

    # No alert — keep the latest pct but stay quiet. A part refill back above
    # the critical level (still low) re-arms that level only.
    state.last_pct = context['pct']
    if state.is_critical and not is_critical:
        state.is_critical = False
    state.save(update_fields=['last_pct', 'is_critical', 'updated_at'])
    return None
