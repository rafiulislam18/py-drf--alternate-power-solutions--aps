"""
Push for Gas Guard low gas.

Sent from the hourly check (``apps.gas_guard.alerts.tasks``) at the same moment
as the low-gas email: only when a cylinder crosses into low or into almost
empty (the ``LowGasAlertState`` latches), not every hour while it stays there,
and not on a refill. Goes to every Gas Guard admin (``GasGuardUser.is_staff``; APS staff are
separate) for every site, and to the site's owner only while that site has an
active Monitoring subscription (low-gas alerts are part of the paid plan; same
rule as the email, ``site_is_monitored``).
"""

import logging

from apps.gas_guard.alerts.emails import almost_empty_subject, left_text
from apps.gas_guard.subscription.models import site_is_monitored
from apps.gas_guard.users.models import GasGuardUser
from apps.gas_guard.weight_scale.models import ScaleDevice

from .models import PushSubscription
from .queue import after_commit
from .send import deliver

logger = logging.getLogger('apps.push')


def queue_low_gas_push(device, site_name, pct, critical=False, est_days=None):
    from .tasks import push_low_gas
    after_commit(push_low_gas, [device.pk, site_name, pct, critical, est_days])


def send_low_gas_push(device_id, site_name, pct, critical=False, est_days=None):
    """``critical``: the "Gas almost empty" alert. Same tag, so it replaces the low one on the device."""
    device = ScaleDevice.objects.select_related('owner').filter(pk=device_id).first()
    if device is None:
        return None
    # A set of accounts: an admin who owns the site gets one push, not two.
    recipients = set(GasGuardUser.objects.filter(is_staff=True, is_active=True).values_list('pk', flat=True))
    if device.owner is not None and device.owner.is_active and site_is_monitored(device):
        recipients.add(device.owner.pk)
    subs = list(PushSubscription.objects.filter(app=PushSubscription.App.GAS_GUARD, gg_user_id__in=recipients))
    if not subs:
        return None
    if critical:
        title, body = almost_empty_subject(site_name), left_text(pct, est_days)
    else:
        title, body = f'Low gas: {site_name}', f'{site_name} is down to {pct}% gas. Arrange a refill before it runs out.'
    payload = {
        'app': 'gas_guard',
        'title': title,
        'body': body,
        'tag': f'gg-site-{device.pk}',
        'url': f'/gas-guard/dashboard/sites/{device.pk}',
    }
    counts = deliver(subs, payload)
    logger.info(f'{"Gas almost empty" if critical else "Low-gas"} push for {site_name} at {pct}%: {counts}')
    return counts
