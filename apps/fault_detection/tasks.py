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
from zoneinfo import ZoneInfo

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .alerts import dispatch_alert

logger = logging.getLogger(__name__)

SOC_IMBALANCE_KEY = 'soc_imbalance'
CHARGE_BEHAVIOUR_KEY = 'charge_behaviour'


def _schedule_now():
    """
    Current time in the schedule's timezone (SAST by default), regardless of
    Django's global TIME_ZONE (which is UTC here). The HA_VALLEY_WINDOWS /
    HA_PEAK_WINDOWS are expressed in this timezone, so the window comparison must
    use it too — otherwise the schedule would be evaluated against UTC and be
    offset by ~2 hours.
    """
    tz_name = getattr(settings, 'HA_SCHEDULE_TIMEZONE', None) or settings.CELERY_TIMEZONE
    return timezone.now().astimezone(ZoneInfo(tz_name))


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


def _fetch_states(entity_ids):
    """
    Fetch several HA entity states in one bulk /api/states call. Returns a dict
    {entity_id: state_string}. Missing entities are simply absent from the dict.
    A single bulk call is faster and less timeout-prone than many per-entity GETs.
    """
    token = settings.HA_TOKEN
    if not token:
        raise HomeAssistantError("HA_TOKEN is not set — cannot authenticate to Home Assistant.")

    base_url = settings.HA_BASE_URL.rstrip('/')
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    try:
        resp = requests.get(f"{base_url}/api/states", headers=headers, timeout=settings.HA_REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HomeAssistantError(f"Failed to reach Home Assistant /api/states: {exc}") from exc

    wanted = set(entity_ids)
    return {s['entity_id']: s.get('state') for s in resp.json() if s['entity_id'] in wanted}


def _parse_window(window):
    """Parse a 'HH:MM-HH:MM' window into ((sh, sm), (eh, em)). Returns None if bad."""
    try:
        start_s, end_s = window.split('-')
        sh, sm = (int(x) for x in start_s.split(':'))
        eh, em = (int(x) for x in end_s.split(':'))
        return (sh, sm), (eh, em)
    except (ValueError, AttributeError):
        logger.warning("Ignoring malformed schedule window %r", window)
        return None


def _now_in_windows(now, windows):
    """
    True if `now` (a localtime datetime) falls within any "HH:MM-HH:MM" window.
    Windows are inclusive of start, exclusive of end, and may wrap past midnight.
    """
    minutes = now.hour * 60 + now.minute
    for window in windows:
        parsed = _parse_window(window)
        if not parsed:
            continue
        (sh, sm), (eh, em) = parsed
        start = sh * 60 + sm
        end = eh * 60 + em
        if start <= end:
            if start <= minutes < end:
                return True
        else:  # wraps midnight, e.g. 22:00-02:00
            if minutes >= start or minutes < end:
                return True
    return False


def _expected_mode_now():
    """
    Determine what APS's hardcoded schedule says the battery should be doing NOW.
    Returns 'valley' (charge), 'peak' (discharge), or None (no expectation).

    This is the SOURCE OF TRUTH — it deliberately ignores the live ems_mode
    sensor, so if the client manually forces a different mode on Home Assistant,
    we still judge the battery against APS's intended schedule and alert.
    """
    now = _schedule_now()
    in_valley = _now_in_windows(now, settings.HA_VALLEY_WINDOWS)
    in_peak = _now_in_windows(now, settings.HA_PEAK_WINDOWS)
    # If the windows ever overlap (misconfiguration), prefer no-expectation rather
    # than guessing, and log it.
    if in_valley and in_peak:
        logger.warning("Schedule windows overlap at %s — treating as no expectation.", now.strftime('%H:%M'))
        return None
    if in_valley:
        return 'valley'
    if in_peak:
        return 'peak'
    return None


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def check_charge_behaviour(self):
    """
    Verify the battery charges during APS's Valley windows and discharges during
    APS's Peak windows. Runs every 5 minutes via Celery Beat.

    SOURCE OF TRUTH = the APS-defined schedule in settings (HA_VALLEY_WINDOWS /
    HA_PEAK_WINDOWS), NOT the live ems_mode sensor. If the client manually forces
    a different mode on Home Assistant, the battery will deviate from APS's
    schedule and we alert — which is the intended behaviour.

    Battery direction comes from sensor.battery_power (VERIFIED 2026-06-30:
    negative = charging, positive = discharging). The per-IES
    charge_and_discharge_status fields are NOT used (stale/unreliable), and the
    ems_mode / inverter charge setting are NOT used to decide faults (they follow
    a manual override and would mask the very fault we want to catch).

    A wrong-direction condition must persist for HA_BEHAVIOUR_PERSIST_COUNT
    consecutive checks before alerting (debounce against brief transients).
    """
    power_e = settings.HA_BATTERY_POWER_ENTITY
    soc_e = settings.HA_BATTERY_SOC_ENTITY
    deadband = settings.HA_BATTERY_POWER_DEADBAND_KW

    expected = _expected_mode_now()  # 'valley' | 'peak' | None
    now_str = _schedule_now().strftime('%d/%m/%Y %H:%M:%S')

    # Outside any scheduled window — no charge/discharge expectation.
    if expected is None:
        msg = f"No scheduled expectation at {now_str} — OK."
        logger.info(msg)
        print(msg)
        _handle_behaviour_transition(faulted=False, context=None)
        return msg

    try:
        states = _fetch_states([power_e, soc_e])
    except HomeAssistantError as exc:
        logger.error("Charge-behaviour check could not run: %s", exc)
        raise self.retry(exc=exc)

    try:
        power = float(states.get(power_e))
    except (TypeError, ValueError):
        msg = f"Battery power unavailable ({states.get(power_e)!r}) — skipping charge-behaviour check."
        logger.warning(msg)
        print(msg)
        return msg

    try:
        soc = float(states.get(soc_e))
    except (TypeError, ValueError):
        soc = None  # SoC-limit guard simply won't apply

    # Determine actual direction from the (verified) power sign + deadband.
    if power <= -deadband:
        actual = 'charging'
    elif power >= deadband:
        actual = 'discharging'
    else:
        actual = 'idle'

    expected_action = 'charging' if expected == 'valley' else 'discharging'

    # SoC-limit guard: at the limit, NOT acting is expected, not a fault.
    at_limit = False
    if soc is not None:
        if expected == 'valley' and soc >= settings.HA_CHARGE_FULL_SOC:
            at_limit = True  # battery full, nothing to charge
        elif expected == 'peak' and soc <= settings.HA_DISCHARGE_FLOOR_SOC:
            at_limit = True  # battery at floor, nothing to discharge

    if at_limit:
        faulted = False
        reason = f"at SoC limit ({soc:.0f}%) — {expected_action} not expected"
    elif actual == 'idle':
        # Idle when it should be charging/discharging: a fault (battery not doing
        # its job), but debounce covers brief idle blips between cycles.
        faulted = True
        reason = f"battery is idle ({power:.1f} kW)"
    else:
        faulted = actual != expected_action
        reason = f"battery is {actual} ({power:.1f} kW)"

    context = {
        'mode': expected,
        'expected': expected_action,
        'actual': actual,
        'power': power,
        'soc': soc,
        'reason': reason,
        'checked_at': now_str,
    }

    if faulted:
        message = (
            f"[ALERT] BATTERY NOT FOLLOWING SCHEDULE - APS schedule ({expected}) "
            f"expects {expected_action}, but {reason}."
        )
        logger.warning(message)
    else:
        message = (
            f"Charge behaviour OK - schedule {expected}, expected {expected_action}, "
            f"actual {actual} ({power:.1f} kW)."
        )
        logger.info(message)
    print(message)

    _handle_behaviour_transition(faulted=faulted, context=context, message=message)
    return message


def _handle_behaviour_transition(faulted, context, message=''):
    """
    Edge-triggered alerting WITH debounce: a fault must persist for
    HA_BEHAVIOUR_PERSIST_COUNT consecutive checks before the alert fires. Any
    non-faulted check resets the counter and (if previously active) sends a
    recovery alert. Alert delivery failures never propagate.
    """
    from .models import AlertState

    state, _ = AlertState.objects.get_or_create(key=CHARGE_BEHAVIOUR_KEY)
    now = timezone.now()
    persist_needed = settings.HA_BEHAVIOUR_PERSIST_COUNT

    if faulted:
        state.consecutive_count = (state.consecutive_count or 0) + 1
        if not state.is_active and state.consecutive_count >= persist_needed:
            # Debounce satisfied: raise the fault.
            state.is_active = True
            state.last_message = message
            state.last_triggered_at = now
            state.save()
            results = dispatch_alert(
                subject="APS Solar Fault: Battery Not Following Schedule",
                context=_behaviour_alert_context(context, is_fault=True),
            )
            logger.warning("Charge-behaviour alert dispatched: %s", results)
        else:
            # Counting up toward the threshold, or already active (stay quiet).
            state.last_message = message
            state.save(update_fields=['consecutive_count', 'last_message', 'updated_at'])
    else:
        was_active = state.is_active
        state.consecutive_count = 0
        state.is_active = False
        state.last_message = message
        if was_active:
            state.last_recovered_at = now
            state.save()
            results = dispatch_alert(
                subject="APS Solar Recovered: Battery Back On Schedule",
                context=_behaviour_alert_context(context, is_fault=False),
            )
            logger.info("Charge-behaviour recovery alert dispatched: %s", results)
        else:
            state.save(update_fields=['consecutive_count', 'is_active', 'last_message', 'updated_at'])


def _behaviour_alert_context(context, is_fault):
    """Shape the behaviour context for the alert renderer."""
    ctx = dict(context or {})
    ctx['is_fault'] = is_fault
    ctx['kind'] = 'charge_behaviour'
    return ctx


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
        'kind': 'soc_imbalance',
        'is_fault': faulted,
        'spread': spread,
        'threshold': threshold,
        'readings': readings,
        'min': (min_entity, min_soc),
        'max': (max_entity, max_soc),
        'checked_at': _schedule_now().strftime('%d/%m/%Y %H:%M:%S'),
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
