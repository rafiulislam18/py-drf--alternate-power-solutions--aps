"""Rate limits for the push endpoints, per account and app (rates in settings)."""

from apps.subscription_portal.throttling import _EmailScopedThrottle


class _PushThrottle(_EmailScopedThrottle):
    def allow_request(self, request, view):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': f'{view.app}-{request.user.pk}'}


class PushWriteThrottle(_PushThrottle):
    """Turning notifications on/off and removing devices."""

    scope = 'push_write'


class PushTestThrottle(_PushThrottle):
    """The "Send a test notification" button."""

    scope = 'push_test'
