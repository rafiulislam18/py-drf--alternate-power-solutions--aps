"""
Cancelling at the end of the paid period.

Gas Guard bills monthly through PayFast (``frequency=3``): PayFast charges on
the same day every month, and each COMPLETE payment notification moves
``last_payment_date`` (``swap_addon_last_payment_date`` for the add-on). So a
payment pays for one calendar month from its date, and the paid period ends on
``last payment + 1 month`` (``paid_until``), the day PayFast would charge next.

Cancelling (in Plans, or a CANCELLED notification from PayFast) stops the
billing at once but keeps the plan and its features until that day:
``ends_at`` / ``swap_addon_ends_at`` hold it, and the hourly ``end_due`` switches
it off then. With no payment on record, or a paid period that already ended
(e.g. PayFast gave up after failed payments), it ends straight away.

Cancelling monitoring also cancels the site's add-on (swaps need monitoring);
the add-on then ends at its own paid date, and never after monitoring.
"""

import logging

from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.utils import timezone

from .models import Subscription

logger = logging.getLogger(__name__)


def paid_until(last_payment):
    """When the month paid by ``last_payment`` runs out (None without a payment)."""
    return last_payment + relativedelta(months=1) if last_payment else None


def _swap_end(sub, now, cap=None):
    """Schedule (or do) the end of the add-on; returns its end, or None if it ended now."""
    until = paid_until(sub.swap_addon_last_payment_date)
    if cap is not None and (until is None or until > cap):
        until = cap
    if until is not None and until > now:
        sub.swap_addon_ends_at = until
        return until
    sub.swap_addon_active = False
    sub.swap_addon_ends_at = None
    return None


@transaction.atomic
def cancel_monitoring(sub, now=None):
    """
    Monitoring was cancelled (billing already stopped). Keeps it active until the
    paid month ends, or ends it now. Returns the end date, or None if it ended now.
    """
    now = now or timezone.now()
    if sub.ends_at:  # already on its way out
        return sub.ends_at
    until = paid_until(sub.last_payment_date)
    if until is not None and until > now:
        sub.ends_at = until
        if sub.swap_addon_active:
            _swap_end(sub, now, cap=until)
        sub.save()
        return until
    _end_monitoring_now(sub)
    return None


@transaction.atomic
def cancel_swap_addon(sub, now=None):
    """The add-on was cancelled (billing already stopped). Returns its end, or None if it ended now."""
    now = now or timezone.now()
    if sub.swap_addon_ends_at:
        return sub.swap_addon_ends_at
    until = _swap_end(sub, now, cap=sub.ends_at)
    sub.save()
    return until


def _end_monitoring_now(sub):
    sub.is_active = False
    sub.ends_at = None
    sub.swap_addon_active = False
    sub.swap_addon_ends_at = None
    sub.save()
    sub.sync_user_tier()


def resume(sub):
    """A new COMPLETE payment arrived for monitoring: no longer on its way out."""
    sub.ends_at = None


def end_due(now=None):
    """Switch off what was cancelled and has reached the end of its paid period.
    Runs hourly (migration gg_subscription 0005). Returns {'monitoring': n, 'swap': n}."""
    now = now or timezone.now()
    ended = {'monitoring': 0, 'swap': 0}
    for sub in Subscription.objects.filter(is_active=True, ends_at__lte=now).select_related('user', 'device'):
        _end_monitoring_now(sub)
        ended['monitoring'] += 1
        logger.info(f'Monitoring ended (paid period over) for {sub}')
    for sub in Subscription.objects.filter(swap_addon_active=True, swap_addon_ends_at__lte=now):
        sub.swap_addon_active = False
        sub.swap_addon_ends_at = None
        sub.save(update_fields=['swap_addon_active', 'swap_addon_ends_at', 'updated_at'])
        ended['swap'] += 1
        logger.info(f'Cylinder-swap add-on ended (paid period over) for {sub}')
    return ended
