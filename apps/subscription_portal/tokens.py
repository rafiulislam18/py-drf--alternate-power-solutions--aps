"""
Scoped, short-lived access tokens for the Manage-Subscriptions portal.

The portal has no user model to bind to (main-site subscribers are anonymous
``Client`` rows), so we issue a self-contained signed JWT instead of a
simplejwt user token. The subscriber's email is baked into the signed payload
and every protected endpoint reads the email FROM THE TOKEN — never from the
request body — so a holder can't tamper it to view or cancel someone else's
subscriptions.

Signed with the project ``SECRET_KEY`` (HS256); ``scope`` pins the token to
this feature so it can't be replayed against another JWT-reading endpoint.
"""

from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone

# 60-minute window: long enough to review and cancel, short enough that a leaked
# token has a small blast radius. There is no refresh — an expired token means
# requesting a fresh code.
ACCESS_TTL = timedelta(minutes=60)

SCOPE = 'manage_subscriptions'
_ALG = 'HS256'


def issue_portal_token(email):
    """Return a signed access token that authorises managing ``email``'s subs."""
    now = timezone.now()
    payload = {
        'email': email,
        'scope': SCOPE,
        'iat': int(now.timestamp()),
        'exp': int((now + ACCESS_TTL).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALG)


class PortalTokenError(Exception):
    """Raised when a portal token is missing, malformed, expired or wrong-scope."""


def email_from_auth_header(auth_header):
    """
    Extract and verify the subscriber email from an ``Authorization`` header.

    Accepts ``Bearer <token>``. Verifies the signature, expiry and scope, and
    returns the email claim. Raises :class:`PortalTokenError` on any problem so
    callers can answer with a uniform 401.
    """
    if not auth_header or not auth_header.startswith('Bearer '):
        raise PortalTokenError('Missing bearer token.')

    token = auth_header[len('Bearer '):].strip()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALG])
    except jwt.ExpiredSignatureError:
        raise PortalTokenError('Your session has expired — request a new code.')
    except jwt.InvalidTokenError:
        raise PortalTokenError('Invalid session token.')

    if payload.get('scope') != SCOPE:
        raise PortalTokenError('Token is not valid for this action.')

    email = payload.get('email')
    if not email:
        raise PortalTokenError('Token is missing its subject.')
    return email
