"""Dashboard sign-in helpers shared by the token view and its throttle."""

from django.contrib.auth.models import User

from apps.core.models import ClientProfile


def email_can_sign_in(user):
    """May this account sign in by typing its email?

    Staff: yes (APS sets their addresses). Clients: once the address is
    verified — plus self-registered accounts that never verified, so the token
    view can tell them to verify instead of claiming the account doesn't exist.
    """
    if user.is_staff or user.is_superuser:
        return True
    try:
        profile = user.client_profile
    except ClientProfile.DoesNotExist:
        return False
    return profile.email_verified or (profile.self_registered and not profile.verified_email)


def resolve_login_identifier(identifier):
    """Map what was typed in the login box to a stored username.

    The dashboard accepts either the username or the account email. A username
    match (case-insensitive) wins; otherwise an email that belongs to exactly
    one account (and may be used to sign in — :func:`email_can_sign_in`) is
    swapped for that account's username. Anything else comes back unchanged so
    authentication fails normally (ambiguous legacy duplicates fail closed
    instead of picking an arbitrary account).
    """
    if not isinstance(identifier, str):
        return identifier
    value = identifier.strip()
    if not value:
        return value
    usernames = list(User.objects.filter(username__iexact=value).values_list('username', flat=True)[:2])
    if usernames:
        # Two usernames differing only by case: leave it to the auth backend,
        # which fails closed on that.
        return usernames[0] if len(usernames) == 1 else value
    if '@' in value:
        matches = list(User.objects.filter(email__iexact=value).select_related('client_profile')[:2])
        if len(matches) == 1 and email_can_sign_in(matches[0]):
            return matches[0].username
    return value


def find_user_by_email(email):
    """The one account whose email is ``email`` (any case), else ``None``."""
    matches = list(User.objects.filter(email__iexact=email).select_related('client_profile')[:2])
    return matches[0] if len(matches) == 1 else None


def identifier_taken(value, exclude_user=None):
    """Is ``value`` already some account's username or email (any case)?"""
    qs = User.objects.all()
    if exclude_user is not None:
        qs = qs.exclude(pk=exclude_user.pk)
    return qs.filter(username__iexact=value).exists() or qs.filter(email__iexact=value).exists()
