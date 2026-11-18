"""
One row per device (browser / installed app) that turned notifications on.

The two apps have separate user tables: the APS dashboard uses Django's
``auth.User`` (staff and clients), Gas Guard its own ``GasGuardUser``. So a row
belongs to exactly one of them, matching ``app`` (a database check enforces it).
"""

import hashlib

from django.conf import settings
from django.db import models
from django.db.models import Q


class PushSubscription(models.Model):
    class App(models.TextChoices):
        APS = 'aps', 'APS dashboard'
        GAS_GUARD = 'gas_guard', 'Gas Guard'

    app = models.CharField(max_length=10, choices=App.choices, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='push_subscriptions',
    )
    gg_user = models.ForeignKey(
        'gg_users.GasGuardUser', on_delete=models.CASCADE, null=True, blank=True, related_name='push_subscriptions',
    )
    # Where the push service takes messages for this device. Unique: the same
    # browser subscribing again (or for another account) updates the row.
    endpoint = models.URLField(max_length=1024, unique=True)
    # The browser's encryption keys (base64url), from PushSubscription.toJSON().
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    device_label = models.CharField(max_length=100, blank=True)  # e.g. "Chrome on Android"
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)  # last notification the push service accepted

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                name='push_subscription_owner_matches_app',
                condition=(
                    Q(app='aps', user__isnull=False, gg_user__isnull=True)
                    | Q(app='gas_guard', user__isnull=True, gg_user__isnull=False)
                ),
            ),
        ]

    def __str__(self):
        owner = self.user if self.app == self.App.APS else self.gg_user
        return f'{self.get_app_display()} · {owner} · {self.device_label or "device"}'

    @property
    def endpoint_hash(self):
        """SHA-256 of the endpoint: the page compares it with its own to mark "This device"."""
        return hashlib.sha256(self.endpoint.encode()).hexdigest()

    def subscription_info(self):
        return {'endpoint': self.endpoint, 'keys': {'p256dh': self.p256dh, 'auth': self.auth}}
