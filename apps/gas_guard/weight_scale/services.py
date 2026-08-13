"""
Site-level domain logic.

A "site" is the dashboard's view of a ScaleDevice: the raw scale weights are
turned into gas-remaining figures using the device's cylinder profile
(``tare_kg`` / ``full_gas_kg``), plus connectivity and consumption stats.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import WeightReading

# A device that hasn't reported for this long is considered offline.
ONLINE_WINDOW = timedelta(minutes=15)

# Days of history used to estimate the remaining-days figure.
ESTIMATE_WINDOW_DAYS = 7

_TO_KG = {
    WeightReading.KILOGRAMS: Decimal('1'),
    WeightReading.GRAMS: Decimal('0.001'),
    WeightReading.POUNDS: Decimal('0.45359237'),
}


def to_kg(weight, unit):
    """Normalise a reading's weight to kilograms as a float."""
    return float(Decimal(weight) * _TO_KG[unit])


def site_payload(device, *, include_estimate, now=None):
    """
    Serialize a ScaleDevice into the Site shape the frontend consumes.

    ``include_estimate`` gates the subscriber-only ``estDaysLeft`` figure.
    """
    now = now or timezone.now()
    latest = device.readings.first()  # model ordering puts newest first

    if latest is not None:
        latest_data = {
            'at': latest.received_at.isoformat(),
            'totalKg': round(to_kg(latest.weight, latest.unit), 2),
        }
        connectivity = {
            'online': now - latest.received_at <= ONLINE_WINDOW,
            'rssiDbm': latest.rssi_dbm,
            'lastSeen': latest.received_at.isoformat(),
        }
    else:
        # No readings yet — report an empty cylinder rather than hiding the
        # site, so a freshly paired device is visible (and flagged) right away.
        latest_data = {
            'at': device.created_at.isoformat(),
            'totalKg': float(device.tare_kg),
        }
        connectivity = {
            'online': False,
            'rssiDbm': None,
            'lastSeen': device.created_at.isoformat(),
        }

    est_days = None
    if include_estimate and latest is not None:
        est_days = estimate_days_left(device, latest, now)

    return {
        'id': str(device.pk),
        'name': device.name or device.device_id or f'Site {device.pk}',
        'tareKg': float(device.tare_kg),
        'fullGasKg': float(device.full_gas_kg),
        'latest': latest_data,
        'connectivity': connectivity,
        'estDaysLeft': est_days,
    }


def daily_consumption(device, days=30, now=None):
    """
    Per-day gas consumption (kg) over the trailing ``days`` days.

    Consumption is the sum of weight *drops* between consecutive readings;
    increases are refills and are ignored. Days without data report 0 so the
    chart always covers the full window.
    """
    now = now or timezone.now()
    start = now - timedelta(days=days)

    readings = list(
        device.readings.filter(received_at__gte=start)
        .order_by('received_at')
        .values_list('weight', 'unit', 'received_at')
    )

    per_day = {}
    for (prev_w, prev_u, _), (cur_w, cur_u, cur_at) in zip(readings, readings[1:]):
        drop = to_kg(prev_w, prev_u) - to_kg(cur_w, cur_u)
        if drop > 0:
            day = timezone.localdate(cur_at)
            per_day[day] = per_day.get(day, 0.0) + drop

    today = timezone.localdate(now)
    return [
        {
            'date': (today - timedelta(days=offset)).isoformat(),
            'kg': round(per_day.get(today - timedelta(days=offset), 0.0), 2),
        }
        for offset in range(days - 1, -1, -1)
    ]


def fleet_daily_consumption(devices, days=30, now=None):
    """Per-day consumption summed across ``devices`` (the dashboard chart)."""
    now = now or timezone.now()
    totals = {}
    for device in devices:
        for day in daily_consumption(device, days=days, now=now):
            totals[day['date']] = totals.get(day['date'], 0.0) + day['kg']
    return [
        {'date': date, 'kg': round(kg, 2)}
        for date, kg in sorted(totals.items())
    ]


def estimate_days_left(device, latest, now):
    """
    Days of gas left at the recent burn rate, or None when there is no
    usage history to extrapolate from.
    """
    gas_kg = max(to_kg(latest.weight, latest.unit) - float(device.tare_kg), 0.0)
    recent = daily_consumption(device, days=ESTIMATE_WINDOW_DAYS, now=now)
    total_used = sum(day['kg'] for day in recent)
    if total_used <= 0:
        return None
    return round(gas_kg / (total_used / ESTIMATE_WINDOW_DAYS))
