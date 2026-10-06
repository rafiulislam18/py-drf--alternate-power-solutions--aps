"""
Rate limits for client tickets and sites. Rates live in settings
``REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']``.

Ticket creation and site adds/edits are keyed per client account, to stop a
runaway script flooding the team's inbox or the site list. Browsing isn't
limited. (Reuses the Manage-Subscriptions throttle base, which also fixes DRF
reading ``"30/h"``-style windows.)
"""

from apps.subscription_portal.throttling import _EmailScopedThrottle


class _ClientWriteThrottle(_EmailScopedThrottle):
    def allow_request(self, request, view):
        # Only writes are limited; browsing is not.
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': f'client-{request.user.pk}'}


class TicketCreateThrottle(_ClientWriteThrottle):
    scope = 'client_portal_ticket_create'


class SiteWriteThrottle(_ClientWriteThrottle):
    scope = 'client_portal_site_write'


class MessageWriteThrottle(_ClientWriteThrottle):
    """Chat messages per account (client or staff); reading/polling is free."""

    scope = 'client_portal_message'
