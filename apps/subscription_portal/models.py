from datetime import timedelta

from django.db import models
from django.utils import timezone

# How long an emailed code stays valid, and the ceiling on wrong guesses before
# the code is burned. Kept as module constants so the views and tests share one
# source of truth.
OTP_TTL = timedelta(minutes=30)
OTP_MAX_ATTEMPTS = 5


class PortalOTP(models.Model):
    """
    A one-time code emailed to prove ownership of a subscriber email address,
    for the public "Manage Your Subscriptions" portal.

    The portal is unauthenticated (no APS account is involved — subscribers of
    the main site are collected via a checkout ``Client``), so the only way to
    gate the manage page is to prove the person can read the email's inbox.
    This row holds one live code per email:

    - 6 digits, valid for :data:`OTP_TTL` (30 min).
    - Single-use: ``consumed`` is set the moment a correct code is verified.
    - Attempt-capped: after :data:`OTP_MAX_ATTEMPTS` wrong guesses the code is
      dead even if still within its TTL, to stop brute-forcing 10**6 codes.

    A successful verify mints a short-lived scoped JWT (see ``tokens.py``); this
    row is not a session — it only proves the inbox once.
    """

    email = models.EmailField(db_index=True)
    code = models.CharField(max_length=6)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Portal OTP'
        verbose_name_plural = 'Portal OTPs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email', 'consumed']),
        ]

    def __str__(self):
        state = 'consumed' if self.consumed else ('expired' if self.is_expired else 'live')
        return f'{self.email} — {state}'

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_live(self):
        """A code that can still be tried: not consumed, not expired, attempts left."""
        return (
            not self.consumed
            and not self.is_expired
            and self.attempts < OTP_MAX_ATTEMPTS
        )
