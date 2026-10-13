"""Who may do what on the dashboard (staff side)."""

from apps.core.roles import is_dashboard_admin

from . import catalogue


def permissions_for(user):
    """The permission codes ``user`` holds (a list; empty for clients / anonymous)."""
    if not is_dashboard_admin(user):
        return []
    if user.is_superuser:
        return list(catalogue.ALL_PERMS)
    cached = getattr(user, '_dashboard_perms', None)
    if cached is None:
        from .models import StaffAccess
        try:
            cached = catalogue.clean(user.staff_access.permissions)
        except StaffAccess.DoesNotExist:
            cached = list(catalogue.DEFAULT_PERMS)
        user._dashboard_perms = cached  # once per request (the user object lives one request)
    return cached


def has_perm(user, code):
    return code in permissions_for(user)


def denied_message(code):
    label = catalogue.LABELS.get(code, code)
    return f"You don't have permission to {label[0].lower()}{label[1:]}. Ask a superuser to give it to you."
