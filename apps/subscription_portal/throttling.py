"""
Rate limits for the public portal endpoints.

The request-OTP and verify-OTP endpoints are unauthenticated and email-driven,
so they're the obvious brute-force / enumeration surface. We throttle per email
(falling back to IP) rather than per authenticated user. Rates are set in
settings ``REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']``:

- ``portal_otp_request`` — 5 / 30 min  (per the product spec)
- ``portal_otp_verify``  — a tighter cap so codes can't be guessed in bulk.
"""

import re

from rest_framework.throttling import SimpleRateThrottle

_RATE_RE = re.compile(r'^(\d+)/(\d*)([smhd])$')
_UNIT_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}


class _EmailScopedThrottle(SimpleRateThrottle):
    """Throttle keyed on the request's ``email`` field, or the client IP.

    Also fixes DRF's ``parse_rate``, which ignores the numeric part of the
    period (``"5/30m"`` would mean 5-per-*minute*). We parse a full
    ``<count>/<N><unit>`` window so ``"5/30m"`` correctly means 5 per 30
    minutes.
    """

    def parse_rate(self, rate):
        if rate is None:
            return (None, None)
        m = _RATE_RE.match(rate)
        if not m:
            return super().parse_rate(rate)
        count, n, unit = m.groups()
        window = int(n or '1') * _UNIT_SECONDS[unit]
        return (int(count), window)

    def get_cache_key(self, request, view):
        email = ''
        if request.method == 'POST' and isinstance(request.data, dict):
            email = (request.data.get('email') or '').strip().lower()
        ident = email or self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class OtpRequestThrottle(_EmailScopedThrottle):
    scope = 'portal_otp_request'


class OtpVerifyThrottle(_EmailScopedThrottle):
    scope = 'portal_otp_verify'
