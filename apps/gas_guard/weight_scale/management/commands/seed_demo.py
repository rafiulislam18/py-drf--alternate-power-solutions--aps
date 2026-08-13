"""
Seed demo accounts, devices and 30 days of weight readings.

Idempotent: running it again wipes and recreates the demo data, so it can be
used to reset the demo environment at any time.

    python manage.py seed_demo
"""

import random
from datetime import timedelta
from decimal import Decimal

from apps.gas_guard.users.models import GasGuardUser
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.gas_guard.weight_scale.models import ScaleDevice, WeightReading

User = GasGuardUser

DEMO_PASSWORD = 'GasGuard2026!'

# (email, first, last, tier, alerts_enabled, notify_email, is_superuser)
#
# admin@gmail.com is the primary preview account: a plain subscriber (not
# staff) so its dashboard shows exactly its own four sites — the mock data
# from the marketing preview — with every subscriber feature unlocked.
DEMO_USERS = [
    ('admin@gmail.com', 'Admin', 'Demo', User.Tier.SUBSCRIBED,
     True, 'admin@gmail.com', False),
    ('ops@alter-power.co.za', 'APS', 'Operations', User.Tier.SUBSCRIBED,
     True, 'alerts@alter-power.co.za', False),
    ('basic@alter-power.co.za', 'Device-only', 'Demo', User.Tier.DEVICE,
     False, '', False),
]

# (owner email, device_id, name, location, daily kg burn rate,
#  current gas kg, rssi dBm, hours since last reading)
#
# admin@gmail.com owns the same four sites shown in the marketing preview /
# landing hero, so the dashboard mirrors that mock data end-to-end.
DEMO_SITES = [
    ('admin@gmail.com', 'esp32-paarl-01', 'Urban Growth — Paarl',
     'Paarl', 1.3, 38.2, -52, 0),
    ('admin@gmail.com', 'esp32-montague-01', 'Urban Growth — Montague',
     'Montague Gardens', 1.6, 14.6, -75, 0),
    ('admin@gmail.com', 'esp32-stuart-01', 'Rennies — Stuart Close',
     'Stuart Close', 1.1, 41.9, -66, 0),
    # Offline: last heard from ~12 h ago, gas nearly out.
    ('admin@gmail.com', 'esp32-wynberg-01', 'APS Office — Wynberg',
     'Wynberg, Cape Town', 1.4, 6.1, -58, 12),
    ('basic@alter-power.co.za', 'esp32-demo-01', 'Demo — Device Tier',
     'Cape Town', 1.2, 22.0, -60, 0),
]

TARE_KG = Decimal('16.00')
FULL_GAS_KG = Decimal('48.00')

HISTORY_DAYS = 35
READINGS_PER_DAY = 4  # every 6 hours


class Command(BaseCommand):
    help = 'Seed demo users, scale devices and 30+ days of readings.'

    @transaction.atomic
    def handle(self, *args, **options):
        users = {}
        for email, first, last, tier, alerts, notify, is_superuser in DEMO_USERS:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={'first_name': first, 'last_name': last},
            )
            user.first_name = first
            user.last_name = last
            user.tier = tier
            user.email_alerts_enabled = alerts
            user.notify_email = notify
            user.is_staff = is_superuser
            user.is_superuser = is_superuser
            user.set_password(DEMO_PASSWORD)
            user.save()
            users[email] = user
            self.stdout.write(
                f"{'Created' if created else 'Updated'} user {email} "
                f"({tier}{', superuser' if is_superuser else ''}) "
                f"— password: {DEMO_PASSWORD}"
            )

        now = timezone.now()
        for owner_email, device_id, name, location, rate, gas_now, rssi, gap_h in DEMO_SITES:
            device, created = ScaleDevice.objects.get_or_create(
                device_id=device_id,
                defaults={'name': name, 'location': location},
            )
            device.name = name
            device.location = location
            device.owner = users[owner_email]
            device.tare_kg = TARE_KG
            device.full_gas_kg = FULL_GAS_KG
            device.is_active = True
            device.save()

            device.readings.all().delete()
            readings = self._build_history(device, rate, gas_now, rssi, gap_h, now)
            WeightReading.objects.bulk_create(readings)

            # Clear any latched low-gas alert state so a re-seed starts fresh:
            # the next hourly check re-evaluates and re-alerts any low sites.
            from apps.gas_guard.alerts.models import LowGasAlertState
            LowGasAlertState.objects.filter(device=device).delete()

            self.stdout.write(
                f"{'Created' if created else 'Updated'} site '{name}' "
                f"({len(readings)} readings) — X-API-Key: {device.api_key}"
            )

        self.stdout.write(self.style.SUCCESS('Demo data seeded.'))

    def _build_history(self, device, daily_rate, gas_now, rssi_base, gap_hours, now):
        """
        Walk backwards from the current gas level, adding consumption per
        6-hour step; when the walk exceeds a full cylinder, that boundary was
        a refill (before it, the cylinder was nearly empty).
        """
        rng = random.Random(device.device_id)  # deterministic per device
        step = timedelta(hours=24 / READINGS_PER_DAY)
        total = float(device.tare_kg) + gas_now
        at = now - timedelta(hours=gap_hours, minutes=rng.randint(1, 5))
        full_total = float(device.tare_kg + device.full_gas_kg)

        readings = []
        for _ in range(HISTORY_DAYS * READINGS_PER_DAY):
            readings.append(
                WeightReading(
                    device=device,
                    weight=Decimal(f'{total:.3f}'),
                    unit=WeightReading.KILOGRAMS,
                    battery_voltage=Decimal(f'{rng.uniform(3.9, 4.2):.2f}'),
                    rssi_dbm=rssi_base + rng.randint(-4, 4),
                    captured_at=at,
                    received_at=at,
                )
            )
            # Going back in time there was *more* gas on the scale.
            total += (daily_rate / READINGS_PER_DAY) * rng.uniform(0.5, 1.5)
            if total > full_total:
                # A refill happened here; before it the cylinder was low.
                total = float(device.tare_kg) + rng.uniform(2.0, 5.0)
            at -= step
        return readings
