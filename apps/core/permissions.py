from rest_framework.permissions import BasePermission

from .roles import is_dashboard_admin, is_dashboard_client


class IsDashboardAdmin(BasePermission):
    """Signed-in APS staff on the dashboard (see apps.core.roles.get_role)."""

    def has_permission(self, request, view):
        return is_dashboard_admin(request.user)


class IsDashboardClient(BasePermission):
    """A signed-in client account (not staff) — tickets, sites, subscriptions."""

    def has_permission(self, request, view):
        return is_dashboard_client(request.user)
