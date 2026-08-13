from django.db import models

from apps.gas_guard.weight_scale.models import ScaleDevice


class LowGasAlertState(models.Model):
    """
    Tracks whether a site currently has an *active* low-gas alert, so the hourly
    check is edge-triggered: it fires once when a cylinder crosses the low
    threshold and again only after a refill lifts it back above (and it drops
    again). Without this, the hourly task would re-alert every run while a
    cylinder stays low.

    One row per device (a "site"). `is_active` is the alert latch.
    """

    device = models.OneToOneField(
        ScaleDevice,
        on_delete=models.CASCADE,
        related_name='low_gas_alert_state',
    )
    # True while the site is below the threshold and has been alerted.
    is_active = models.BooleanField(default=False)
    # Percentage remaining recorded at the last state change (for the message).
    last_pct = models.IntegerField(null=True, blank=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_recovered_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        state = 'LOW' if self.is_active else 'ok'
        return f'{self.device} — {state}'
