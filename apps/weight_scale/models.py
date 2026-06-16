import secrets

from django.db import models
from django.utils import timezone


def generate_api_key():
    """Generate a random API key used by a device to authenticate."""
    return secrets.token_hex(32)


class ScaleDevice(models.Model):
    """An ESP32-based weight scale that reports readings to the API."""

    device_id = models.CharField(
        max_length=64,
        unique=True,
        help_text='Stable hardware identifier, e.g. the ESP32 MAC address.',
    )
    name = models.CharField(max_length=120, blank=True)
    location = models.CharField(max_length=200, blank=True)
    api_key = models.CharField(
        max_length=64,
        unique=True,
        default=generate_api_key,
        db_index=True,
        help_text='Secret key the device sends in the X-API-Key header.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'device_id']

    def __str__(self):
        return self.name or self.device_id


class WeightReading(models.Model):
    """A single weight measurement reported by a scale device."""

    KILOGRAMS = 'kg'
    GRAMS = 'g'
    POUNDS = 'lb'
    UNIT_CHOICES = [
        (KILOGRAMS, 'Kilograms'),
        (GRAMS, 'Grams'),
        (POUNDS, 'Pounds'),
    ]

    device = models.ForeignKey(
        ScaleDevice,
        on_delete=models.CASCADE,
        related_name='readings',
    )
    weight = models.DecimalField(max_digits=10, decimal_places=3)
    unit = models.CharField(max_length=4, choices=UNIT_CHOICES, default=KILOGRAMS)
    # Optional raw load-cell value before calibration, handy for debugging drift.
    raw_value = models.IntegerField(null=True, blank=True)
    battery_voltage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    # Timestamp from the device — may be unreliable if the ESP32 has no NTP sync.
    captured_at = models.DateTimeField(null=True, blank=True)
    # Authoritative server-side timestamp.
    received_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['device', '-received_at']),
        ]

    def __str__(self):
        return f'{self.device} — {self.weight}{self.unit} @ {self.received_at:%Y-%m-%d %H:%M}'
