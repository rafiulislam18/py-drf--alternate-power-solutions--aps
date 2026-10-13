"""
DRF permission for the staff dashboard: signed-in staff holding the right
permission (superusers always pass; clients never do).

A view says what it needs in one of three ways:

- ``dashboard_section = 'blogs'`` — the usual CRUD mapping: GET → view,
  POST → create, PUT/PATCH → edit, DELETE → delete;
- ``dashboard_perms = {'GET': 'tickets.view', 'POST': 'tickets.create'}``;
- ``def dashboard_perm(self, request)`` returning a code, a list of codes
  (all needed) or ``None`` (any staff member).

A method the view doesn't map is refused.
"""

from rest_framework.permissions import BasePermission

from apps.core.roles import is_dashboard_admin

from .access import denied_message, has_perm

CRUD = {'GET': 'view', 'POST': 'create', 'PUT': 'edit', 'PATCH': 'edit', 'DELETE': 'delete'}
_MISSING = object()


def required_perms(view, request):
    method = 'GET' if request.method == 'HEAD' else request.method
    if hasattr(view, 'dashboard_perm'):
        codes = view.dashboard_perm(request)
    elif getattr(view, 'dashboard_perms', None) is not None:
        codes = view.dashboard_perms.get(method, _MISSING)
    elif getattr(view, 'dashboard_section', None):
        action = CRUD.get(method)
        codes = f'{view.dashboard_section}.{action}' if action else _MISSING
    else:
        codes = _MISSING
    if codes is None:
        return []
    if codes is _MISSING:
        return None
    return [codes] if isinstance(codes, str) else list(codes)


class HasDashboardPerm(BasePermission):
    message = "You don't have permission to do that."

    def has_permission(self, request, view):
        if not is_dashboard_admin(request.user):
            return False
        if request.method == 'OPTIONS':
            return True
        codes = required_perms(view, request)
        if codes is None:
            return False
        for code in codes:
            if not has_perm(request.user, code):
                self.message = denied_message(code)
                return False
        return True


class IsSuperuser(BasePermission):
    message = 'Only a superuser can manage staff accounts.'

    def has_permission(self, request, view):
        return bool(is_dashboard_admin(request.user) and request.user.is_superuser)
