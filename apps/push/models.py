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


class NotificationPrefs(models.Model):
    """One APS dashboard account's notification settings (no row = the defaults).

    - ``off_types``: the push types this person switched off in Account settings
      (``apps.push.prefs.TYPES``); everything else is on.
    - ``admin_alerts``: "Receive admin alerts", set on the Staff accounts page by
      whoever manages staff. Not a permission (superusers have every permission
      but still only get admin alerts when this is on). Off by default.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_prefs')
    off_types = models.JSONField(default=list, blank=True)
    admin_alerts = models.BooleanField('receive admin alerts', default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'notification settings'
        verbose_name_plural = 'notification settings'

    def __str__(self):
        return f'{self.user}: admin alerts {"on" if self.admin_alerts else "off"}, off: {", ".join(self.off_types) or "none"}'
