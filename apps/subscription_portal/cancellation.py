"""
Cancel one recurring subscription on behalf of a proven email address.

Shared by the public Manage-Subscriptions portal (``views.cancel``) and the
client portal's Subscriptions page (``apps.client_portal``), so both cancel
identically: an ownership re-check, then the real PayFast cancel, then the
local deactivation. Callers only differ in how they proved the email.
"""

import logging

from django.core.signing import BadSignature
from django.db import transaction
from rest_framework import status

from .payfast import cancel_payfast_subscription
from .providers import get_owned_subscription

logger = logging.getLogger('apps.subscription_portal')


def cancel_for_email(email, ref, source='portal'):
    """
    Cancel the subscription behind ``ref`` if ``email`` owns it.

    Returns ``(http_status, detail)`` for the caller to send back. Access is
    kept until the paid period ends — we only set ``is_active`` False.
    """
    ref = (ref or '').strip()
    if not ref:
        return status.HTTP_400_BAD_REQUEST, 'Missing subscription reference.'

    try:
        sub = get_owned_subscription(email, ref)
    except (BadSignature, ValueError):
        return status.HTTP_400_BAD_REQUEST, 'Invalid subscription reference.'

    if sub is None:
        # Either the row is gone or it isn't owned by this email. Same 403 either
        # way so we don't leak which.
        return status.HTTP_403_FORBIDDEN, 'You are not authorised to cancel this subscription.'

    if not sub.is_active:
        return status.HTTP_400_BAD_REQUEST, 'This subscription is already inactive.'

    # Real cancel at PayFast first — if that fails, don't touch our DB so the
    # user (and PayFast) stay consistent and they can retry.
    if not cancel_payfast_subscription(sub.payfast_token):
        return (
            status.HTTP_502_BAD_GATEWAY,
            'We couldn\'t cancel this subscription with the payment provider '
            'right now. Please try again shortly.',
        )

    with transaction.atomic():
        sub.is_active = False
        sub.save(update_fields=['is_active', 'updated_at'])

    logger.info(f'{source} cancel: {email} cancelled {ref}')
    return (
        status.HTTP_200_OK,
        'Your subscription has been cancelled. You keep access until the end of '
        'your current paid period.',
    )
