"""
Sending a Web Push notification (VAPID + pywebpush, no third-party service).

``deliver(subscriptions, payload)`` encrypts ``payload`` (a small JSON dict the
service worker turns into a notification) for each device and hands it to that
browser's push service. The push service's answer decides what happens next:

- accepted → ``last_used_at`` moves on;
- 404 / 410 (the browser dropped the subscription, e.g. permission removed or
  the app uninstalled) → the row is deleted, so it's never tried again;
- anything else → logged and left alone (it may be a passing outage).

Keys come from the environment (``VAPID_PUBLIC_KEY`` / ``VAPID_PRIVATE_KEY`` /
``VAPID_SUBJECT``; make them with ``manage.py generate_vapid_keys``). Without
them nothing is sent and the pages say notifications aren't set up.

Payload shape (both service workers read it)::

    {"app": "aps" | "gas_guard", "title": "...", "body": "...",
     "tag": "aps-ticket-7", "url": "/dashboard/tickets/7"}
"""

import json
import logging
import re

from django.conf import settings
from django.utils import timezone
from pywebpush import WebPushException, webpush

from .models import PushSubscription

logger = logging.getLogger('apps.push')

# Push services hold an undelivered message this long (seconds) for a phone
# that's off or offline; after that it's dropped.
DEFAULT_TTL = 24 * 60 * 60
REQUEST_TIMEOUT = 10
GONE = (404, 410)
_TOPIC_RE = re.compile(r'[^A-Za-z0-9_-]')


def is_configured():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY and settings.VAPID_SUBJECT)


def _topic(tag):
    """The Web Push ``Topic`` header: a waiting message with the same topic is
    replaced, so a phone that was offline gets only the latest one per tag.
    At most 32 URL-safe base64 characters."""
    return _TOPIC_RE.sub('', tag or '')[:32] or None


def send_one(sub, payload, ttl=DEFAULT_TTL, urgency='high'):
    """Send to one device. Returns 'sent', 'gone' (row deleted) or 'failed'."""
    headers = {'Urgency': urgency}
    topic = _topic(payload.get('tag'))
    if topic:
        headers['Topic'] = topic
    try:
        webpush(
            subscription_info=sub.subscription_info(),
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            # A new dict every time: pywebpush writes this endpoint's "aud" into it.
            vapid_claims={'sub': settings.VAPID_SUBJECT},
            ttl=ttl,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except WebPushException as exc:
        status = getattr(exc.response, 'status_code', None)
        if status in GONE:
            logger.info(f'Push subscription {sub.pk} ({sub.device_label}) is gone ({status}); removed.')
            sub.delete()
            return 'gone'
        logger.warning(f'Push to subscription {sub.pk} failed ({status}): {exc}')
        return 'failed'
    except Exception as exc:  # noqa: BLE001 — network error, bad key …: never break the caller
        logger.warning(f'Push to subscription {sub.pk} failed: {exc}')
        return 'failed'
    PushSubscription.objects.filter(pk=sub.pk).update(last_used_at=timezone.now())
    return 'sent'


def deliver(subscriptions, payload, **kwargs):
    """Send ``payload`` to every subscription. Returns {'sent', 'gone', 'failed'} counts."""
    counts = {'sent': 0, 'gone': 0, 'failed': 0}
    if not is_configured():
        logger.info('VAPID keys not set — push notifications are off.')
        return counts
    for sub in subscriptions:
        counts[send_one(sub, payload, **kwargs)] += 1
    return counts
