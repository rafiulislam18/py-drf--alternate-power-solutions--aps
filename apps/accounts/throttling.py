"""
Rate limits for the public account endpoints (rates in settings
REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'], windows like "5/30m"):

- ``accounts_ip``       — per client IP across sign-up, verify, resend, forgot and
  reset, so one machine can't mass-create accounts or spray emails.
- ``accounts_email``    — per target email for anything that SENDS an email
  (sign-up, resend verification, forgot password), so nobody can flood an inbox.
- ``accounts_password`` — per signed-in account for change-password / change-email
  (both check the current password), so a stolen session can't brute-force it.
"""

from apps.solar_dashboard.throttling import _WindowRateThrottle


class AccountsIpThrottle(_WindowRateThrottle):
    scope = 'accounts_ip'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class AccountsEmailThrottle(_WindowRateThrottle):
    scope = 'accounts_email'

    def get_cache_key(self, request, view):
        data = request.data if isinstance(request.data, dict) else {}
        email = data.get('email')
        if not isinstance(email, str) or not email.strip():
            return None  # the view answers 400; the IP throttle still applies
        return self.cache_format % {'scope': self.scope, 'ident': email.strip().lower()[:254]}


class AccountsPasswordThrottle(_WindowRateThrottle):
    scope = 'accounts_password'

    def get_cache_key(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return None
        return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}
