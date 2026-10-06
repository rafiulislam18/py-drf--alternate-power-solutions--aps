"""
Tokens for the dashboard's emailed links and sign-in sessions.

Emailed links carry a ``django.core.signing`` token (signed with SECRET_KEY,
timestamped), so nothing is stored server-side:

- **verify** ``{u, e}`` — confirms that user ``u`` owns address ``e``. Valid for
  3 days. Only counts while ``e`` is still the account's email (or the pending
  new email), so changing the address kills older links.
- **reset** ``{u, s}`` — sets a new password. Valid for 1 hour. ``s`` is a hash
  of the current password and email, so the link dies once it's been used (the
  password changes) or the email changes.
- **claim** ``{e}`` — creates an account for someone who already pays an APS
  subscription under ``e`` but has no login yet. Valid for 1 hour; refused once
  any account has that email, so it works once.

Sign-in JWTs carry ``pv`` (:func:`password_version`): a hash of the password.
Changing or resetting the password changes it, which ends every other session
straight away (see ``authentication.py``).
"""

from django.core import signing
from django.utils.crypto import constant_time_compare, salted_hmac

VERIFY_MAX_AGE = 3 * 24 * 60 * 60
RESET_MAX_AGE = 60 * 60

_VERIFY_SALT = 'apps.accounts.verify-email'
_RESET_SALT = 'apps.accounts.reset-password'


def password_version(user):
    return salted_hmac('apps.accounts.jwt-pv', f'{user.pk}|{user.password}').hexdigest()[:16]


def _reset_state(user):
    return salted_hmac('apps.accounts.reset-state', f'{user.pk}|{user.password}|{user.email}').hexdigest()[:24]


def make_verify_token(user, email):
    return signing.dumps({'u': user.pk, 'e': email.strip().lower()}, salt=_VERIFY_SALT, compress=True)


def read_verify_token(token):
    """``(user_id, email)`` or ``None`` if the token is bad or expired."""
    try:
        data = signing.loads(str(token), salt=_VERIFY_SALT, max_age=VERIFY_MAX_AGE)
        return int(data['u']), str(data['e'])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None


def make_reset_token(user):
    return signing.dumps({'k': 'reset', 'u': user.pk, 's': _reset_state(user)}, salt=_RESET_SALT, compress=True)


def make_claim_token(email):
    return signing.dumps({'k': 'claim', 'e': email.strip().lower()}, salt=_RESET_SALT, compress=True)


def read_reset_token(token):
    """``('reset', user_id, state)`` / ``('claim', email, None)`` / ``None``."""
    try:
        data = signing.loads(str(token), salt=_RESET_SALT, max_age=RESET_MAX_AGE)
        if data.get('k') == 'reset':
            return 'reset', int(data['u']), str(data['s'])
        if data.get('k') == 'claim':
            return 'claim', str(data['e']), None
    except (signing.BadSignature, KeyError, TypeError, ValueError, AttributeError):
        pass
    return None


def reset_state_matches(user, state):
    return constant_time_compare(_reset_state(user), state)
