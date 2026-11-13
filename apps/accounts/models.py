"""
Per-account dashboard settings, for staff and clients alike (staff have no
ClientProfile, so these live in their own table).
"""

from django.conf import settings
from django.db import models


class AccountPreferences(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='dashboard_prefs')
    # How the dashboard looks for this account (theme, font, density, text size,
    # high contrast, reduced motion) — see apps.accounts.appearance. Null until
    # the account first saves one, so the dashboard can tell "never chosen".
    appearance = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Account preferences'
        verbose_name_plural = 'Account preferences'

    def __str__(self):
        return f'Preferences for {self.user}'
