"""Who is staff and who is a client on the dashboard."""

from .models import ClientProfile


def get_role(user):
    """'admin' or 'client' for a signed-in dashboard user.

    A ClientProfile decides. Without one, only Django staff/superusers are
    admins — anyone else (e.g. an account made in the admin without ticking
    "staff") is treated as a client, so a missing profile never grants staff
    access. Accounts that were profile-less admins before this rule got an
    explicit role='admin' profile in core migration 0003.
    """
    try:
        return user.client_profile.role
    except ClientProfile.DoesNotExist:
        return 'admin' if (user.is_staff or user.is_superuser) else 'client'


def is_dashboard_admin(user):
    return bool(user and user.is_authenticated) and get_role(user) == 'admin'


def is_dashboard_client(user):
    return bool(user and user.is_authenticated) and get_role(user) == 'client'
