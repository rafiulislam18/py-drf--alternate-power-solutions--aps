import uuid

from django.db import models


class AlertState(models.Model):
    """
    Tracks whether a named fault condition is currently active, so we alert once
    when it starts (and once when it recovers) instead of every 5-minute cycle.

    One row per fault key (e.g. "soc_imbalance"). `is_active` flips on when the
    fault is first detected and off when it clears.
    """
    key = models.CharField(max_length=100, unique=True, db_index=True)
    is_active = models.BooleanField(default=False)
    # Consecutive checks the fault condition has held (debounce before alerting).
    consecutive_count = models.PositiveSmallIntegerField(default=0)
    last_message = models.TextField(blank=True, default='')
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_recovered_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        status = 'ACTIVE' if self.is_active else 'clear'
        return f"{self.key} ({status})"


class FaultAlert(models.Model):
    """
    A single dispatched fault alert, kept so the on-call team can confirm who is
    handling it. Created on the rising edge of a fault (when the alert is sent),
    it carries a public UUID that goes into the email/Telegram pickup link.

    The pickup page reads this record: if `picked_up_by` is still blank the
    responder enters their name to claim it (recorded once); if it's already set
    the page shows who claimed it and when. Claiming is one-shot — the first
    person to submit locks it, and a "picked up by X" confirmation is sent.
    """
    KIND_CHOICES = (
        ('soc_imbalance', 'SOC Imbalance'),
        ('charge_behaviour', 'Charge Behaviour'),
    )

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    kind = models.CharField(max_length=50, choices=KIND_CHOICES)
    # Human-readable summary of the fault (the same text logged/printed).
    summary = models.TextField(blank=True, default='')
    triggered_at = models.DateTimeField(auto_now_add=True)

    # Pickup tracking. `picked_up_by` blank == nobody has claimed it yet.
    picked_up_by = models.CharField(max_length=120, blank=True, default='')
    picked_up_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-triggered_at']

    @property
    def is_picked_up(self):
        return bool(self.picked_up_by)

    def __str__(self):
        who = self.picked_up_by or 'unclaimed'
        return f"{self.get_kind_display()} @ {self.triggered_at:%Y-%m-%d %H:%M} ({who})"
