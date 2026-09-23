"""
Rate limits for Gas Guard's email-code endpoints.

Every endpoint that *sends* a 6-digit code is an email-bombing and enumeration
surface, so all of them are throttled per email address (falling back to the
client IP when no email is supplied). The endpoints that *verify* a code get a
looser cap — a user mistyping a code shouldn't be locked out as quickly as a
bulk guesser, and each code is separately burned after
``PasswordResetCode.MAX_ATTEMPTS`` wrong guesses.

Rates live in settings ``REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']``:

- ``gg_code_send``   — 5 / min per email (registration, resend, both password
  flows).
- ``gg_code_verify`` — 10 / min per email.

The ``parse_rate`` override is shared with the subscription portal's throttles:
DRF ignores the numeric part of a period, so ``"5/30m"`` would otherwise mean
5-per-minute. Plain ``"5/min"`` parses correctly either way, but keeping the
fix means the rate can be widened to a longer window later without a surprise.
"""

import re

from rest_framework.throttling import SimpleRateThrottle

_RATE_RE = re.compile(r'^(\d+)/(\d*)([smhd])$')
_UNIT_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}


class _EmailScopedThrottle(SimpleRateThrottle):
    """Throttle keyed on the request's ``email`` field, or the client IP."""

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
        # Signed-in flows don't post an email — key on the account instead, so
        # one user can't dodge the limit by omitting it.
        if not email:
            user = getattr(request, 'user', None)
            if user is not None and getattr(user, 'is_authenticated', False):
                email = (getattr(user, 'email', '') or '').strip().lower()
        ident = email or self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class CodeSendThrottle(_EmailScopedThrottle):
    """Any endpoint that emails a 6-digit code."""

    scope = 'gg_code_send'


class CodeVerifyThrottle(_EmailScopedThrottle):
    """Any endpoint that checks a 6-digit code."""

    scope = 'gg_code_verify'
