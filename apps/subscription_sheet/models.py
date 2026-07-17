from django.db import models


class ExportedSubscription(models.Model):
    """
    Tracks which subscriptions have been pushed to the APS Subscriptions Google
    Sheet, so the sync knows what's new vs. already-there.

    Data lives in the two source apps (subscription.Subscription and
    request_solar_cleaning.Subscription); those tables share PK ranges, so we key
    on (app_label, subscription_id). The sheet upserts by the same composite key
    ("<app_label>:<id>"), refreshing date columns on existing rows while leaving
    the admin-edited Comments column untouched.
    """
    APP_MONITORING = 'monitoring'   # apps.subscription -> Inverter & Battery Monitoring Plan
    APP_MAINTENANCE = 'maintenance'  # apps.request_solar_cleaning -> Solar & Inverter Maintenance Plan
    APP_CHOICES = (
        (APP_MONITORING, 'Inverter & Battery Monitoring Plan'),
        (APP_MAINTENANCE, 'Solar & Inverter Maintenance Plan'),
    )

    app_label = models.CharField(max_length=20, choices=APP_CHOICES)
    subscription_id = models.PositiveIntegerField()
    first_exported_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['app_label', 'subscription_id'],
                name='uniq_exported_subscription',
            ),
        ]

    @property
    def key(self):
        return f"{self.app_label}:{self.subscription_id}"

    def __str__(self):
        return self.key
