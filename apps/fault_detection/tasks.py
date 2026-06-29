"""
Automated fault detection for the Home Assistant solar dashboard.

Every 5 minutes (scheduled via django-celery-beat) we poll the State of Charge
(SOC) of all 6 IES battery banks from Home Assistant. If the spread between the
highest and lowest SOC exceeds a configured threshold (default 10 %), we flag a
potential fault — for now this is just a print/log, alerting can be added later.

All connection details and entity IDs are configurable via settings/env so the
real Home Assistant entity IDs can be supplied without code changes.
"""

import logging

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .alerts import dispatch_alert

logger = logging.getLogger(__name__)

SOC_IMBALANCE_KEY = 'soc_imbalance'


class HomeAssistantError(Exception):
    """Raised when Home Assistant cannot be reached or returns bad data."""


def _fetch_soc_values():
    """
    Query Home Assistant for the SOC of each configured IES entity.

    Returns a list of (entity_id, soc_float) tuples for entities that returned
    a usable numeric state. Entities that are missing or unavailable are logged
    and skipped (a dead/offline bank is itself worth knowing about, but it
    should not crash the spread calculation).
    """
    base_url = settings.HA_BASE_URL.rstrip('/')
    token = settings.HA_TOKEN
    entity_ids = settings.HA_SOC_ENTITY_IDS

    if not token:
        raise HomeAssistantError("HA_TOKEN is not set — cannot authenticate to Home Assistant.")
    if not entity_ids:
        raise HomeAssistantError("HA_SOC_ENTITY_IDS is empty — no SOC sensors configured.")

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    readings = []
    for entity_id in entity_ids:
        url = f"{base_url}/api/states/{entity_id}"
        try:
            resp = requests.get(url, headers=headers, timeout=settings.HA_REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise HomeAssistantError(f"Failed to reach Home Assistant for {entity_id}: {exc}") from exc

        data = resp.json()
        state = data.get('state')

        # HA reports 'unavailable' / 'unknown' for offline or undefined sensors.
        if state in (None, '', 'unavailable', 'unknown'):
            logger.warning("SOC sensor %s is %s — skipping.", entity_id, state)
            continue

        try:
            readings.append((entity_id, float(state)))
        except (TypeError, ValueError):
            logger.warning("SOC sensor %s returned non-numeric state %r — skipping.", entity_id, state)
            continue

    return readings


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def check_soc_imbalance(self):
    """
    Poll all IES SOC sensors and flag an imbalance if (max - min) exceeds the
    configured threshold. Intended to run every 5 minutes via Celery Beat.
    """
    threshold = settings.HA_SOC_IMBALANCE_THRESHOLD

    try:
        readings = _fetch_soc_values()
    except HomeAssistantError as exc:
        logger.error("SOC check could not run: %s", exc)
        # Transient network/HA hiccups are worth one retry; config errors are not,
        # but retrying a config error twice is cheap and self-heals if env is fixed.
        raise self.retry(exc=exc)

    if len(readings) < 2:
        msg = (
            f"Only {len(readings)} usable SOC reading(s) - need at least 2 to "
            f"compare. Check that the IES sensors are online."
        )
        logger.warning(msg)
        print(msg)
        return msg

    min_entity, min_soc = min(readings, key=lambda r: r[1])
    max_entity, max_soc = max(readings, key=lambda r: r[1])
    spread = round(max_soc - min_soc, 2)

    summary = ", ".join(f"{eid}={soc:.2f}%" for eid, soc in readings)
    faulted = spread > threshold

    # Structured context used to render the email / Telegram messages.
    context = {
        'is_fault': faulted,
        'spread': spread,
        'threshold': threshold,
        'readings': readings,
        'min': (min_entity, min_soc),
        'max': (max_entity, max_soc),
        'checked_at': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S'),
    }

    if faulted:
        message = (
            f"[ALERT] SOC IMBALANCE DETECTED - spread {spread:.2f}% exceeds "
            f"threshold {threshold:.2f}%. "
            f"Lowest: {min_entity} ({min_soc:.2f}%), "
            f"Highest: {max_entity} ({max_soc:.2f}%). "
            f"All readings: {summary}"
        )
        logger.warning(message)
        print(message)
    else:
        message = (
            f"SOC OK - spread {spread:.2f}% within threshold {threshold:.2f}%. "
            f"Readings: {summary}"
        )
        logger.info(message)
        print(message)

    _handle_transition(SOC_IMBALANCE_KEY, faulted=faulted, message=message, context=context)
    return message


def _handle_transition(key, faulted, message, context):
    """
    Compare the current fault state with the stored state and fire an alert only
    on a transition (clear -> fault, or fault -> clear). Avoids re-alerting every
    cycle while a fault persists. Alert delivery failures never propagate.
    """
    from .models import AlertState

    state, _ = AlertState.objects.get_or_create(key=key)
    now = timezone.now()

    if faulted and not state.is_active:
        # Rising edge: fault just started.
        state.is_active = True
        state.last_message = message
        state.last_triggered_at = now
        state.save()
        results = dispatch_alert(subject="APS Solar Fault: SOC Imbalance Detected", context=context)
        logger.warning("SOC imbalance alert dispatched: %s", results)

    elif not faulted and state.is_active:
        # Falling edge: fault recovered.
        state.is_active = False
        state.last_message = message
        state.last_recovered_at = now
        state.save()
        results = dispatch_alert(subject="APS Solar Recovered: SOC Imbalance Cleared", context=context)
        logger.info("SOC recovery alert dispatched: %s", results)

    else:
        # No transition — keep the latest message but stay quiet.
        state.last_message = message
        state.save(update_fields=['last_message', 'updated_at'])
