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
    last_message = models.TextField(blank=True, default='')
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_recovered_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        status = 'ACTIVE' if self.is_active else 'clear'
        return f"{self.key} ({status})"
