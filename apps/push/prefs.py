"""
Who gets which APS push notification, and each person's own switches.

Types (Account settings → Notifications shows only the ones a person can get):

- ``ticket_message``: clients always; staff with ``tickets.chat_view``;
- ``job_assigned`` and ``visits_today``: staff with ``jobs.view``;
- ``tool_overdue``: staff with ``tools.view``.

"Receive admin alerts" (``NotificationPrefs.admin_alerts``) is a separate
per-account switch, not a permission: accounts with it on get the team-wide
alerts (every overdue booking, all of today's visits), as long as they can see
that area. Someone's own switches always win.
"""

from django.contrib.auth import get_user_model

from apps.core.roles import is_dashboard_admin, is_dashboard_client
from apps.staff.access import has_perm

from .models import NotificationPrefs, PushSubscription

TICKET_MESSAGE, JOB_ASSIGNED, TOOL_OVERDUE, VISITS_TODAY = 'ticket_message', 'job_assigned', 'tool_overdue', 'visits_today'
TYPES = [
    (TICKET_MESSAGE, 'Ticket messages'),
    (JOB_ASSIGNED, 'Job assigned to you'),
    (VISITS_TODAY, 'Visits today'),
    (TOOL_OVERDUE, 'Overdue tools'),
]
LABELS = dict(TYPES)
# What a staff member needs to see each type's page.
STAFF_PERM = {TICKET_MESSAGE: 'tickets.chat_view', JOB_ASSIGNED: 'jobs.view', VISITS_TODAY: 'jobs.view',
              TOOL_OVERDUE: 'tools.view'}


def can_receive(user, kind):
    if not (user and user.is_active):
        return False
    if is_dashboard_client(user):
        return kind == TICKET_MESSAGE
    return is_dashboard_admin(user) and has_perm(user, STAFF_PERM[kind])


def available_types(user):
    return [key for key, _ in TYPES if can_receive(user, key)]


def prefs_for(user):
    """The saved row, or an unsaved one with the defaults."""
    return NotificationPrefs.objects.filter(user=user).first() or NotificationPrefs(user=user)


def wants(user, kind):
    """Can get ``kind`` and hasn't switched it off."""
    return can_receive(user, kind) and kind not in prefs_for(user).off_types


def admin_alert_users(kind):
    """Active staff with "Receive admin alerts" on who can see ``kind``'s page."""
    users = get_user_model().objects.filter(notification_prefs__admin_alerts=True, is_active=True)
    return [u for u in users.select_related('client_profile', 'staff_access') if can_receive(u, kind)]


def staff_with_perm(code):
    """Active staff accounts holding ``code`` (superusers always do)."""
    from apps.staff.views import staff_users
    return [u for u in staff_users().filter(is_active=True) if has_perm(u, code)]


def subscriptions_for(users, kind):
    """APS devices of the people in ``users`` who want ``kind`` (each person once)."""
    ids = {u.pk for u in users if wants(u, kind)}
    return list(PushSubscription.objects.filter(app=PushSubscription.App.APS, user_id__in=ids))
