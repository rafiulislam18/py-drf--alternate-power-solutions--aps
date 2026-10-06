"""
Rate limits for the dashboard's password login (/dashboard/auth/token/).

Two scopes (rates in settings REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']):
- ``dashboard_login_ip``   — per client IP, so one machine can't spray passwords.
- ``dashboard_login_user`` — per account (username or email typed), so one account
  can't be brute-forced from many IPs (share links show the company, so the account is findable).
"""

from rest_framework.throttling import SimpleRateThrottle

from apps.subscription_portal.throttling import _RATE_RE, _UNIT_SECONDS

from apps.accounts.login import resolve_login_identifier


class _WindowRateThrottle(SimpleRateThrottle):
    """Reads "<count>/<N><unit>" windows ("10/15m" = 10 per 15 minutes)."""

    def parse_rate(self, rate):
        if rate is None:
            return (None, None)
        m = _RATE_RE.match(rate)
        if not m:
            return super().parse_rate(rate)
        count, n, unit = m.groups()
        return (int(count), int(n or '1') * _UNIT_SECONDS[unit])


class LoginIpThrottle(_WindowRateThrottle):
    scope = 'dashboard_login_ip'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class LoginUsernameThrottle(_WindowRateThrottle):
    scope = 'dashboard_login_user'

    def get_cache_key(self, request, view):
        data = request.data if isinstance(request.data, dict) else {}
        username = data.get('username')
        if not isinstance(username, str) or not username.strip():
            return None  # nothing to key on; the IP throttle still applies
        # Username and email share one budget: key on the account they resolve to.
        ident = resolve_login_identifier(username)
        return self.cache_format % {'scope': self.scope, 'ident': ident.lower()[:150]}
