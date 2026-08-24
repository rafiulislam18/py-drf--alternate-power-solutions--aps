"""
PayFast recurring-subscription cancellation for the portal.

Ported from ``apps.gas_guard.subscription.views.cancel_payfast_subscription``
so both flows cancel identically: a signed PUT to the PayFast REST API's
``/subscriptions/{token}/cancel`` endpoint. This is the real, production cancel
— it stops PayFast from taking the next recurring charge. The caller is
responsible for the local DB deactivation after this returns True.
"""

import hashlib
import logging
import urllib.parse
from collections import OrderedDict
from datetime import datetime, timezone as dt_timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings

logger = logging.getLogger('apps.subscription_portal')


def _api_signature(headers, passphrase=''):
    """
    MD5 signature for a PayFast REST API request.

    The API signs the request HEADERS (not form fields): sort by key, urlencode
    values, join with '&', append the passphrase. Mirrors the gas_guard helper.
    """
    ordered = OrderedDict(sorted(headers.items(), key=lambda kv: kv[0]))
    payload = '&'.join(
        f'{k}={urllib.parse.quote_plus(str(v).strip())}' for k, v in ordered.items()
    )
    if passphrase:
        payload += f'&passphrase={urllib.parse.quote_plus(passphrase.strip())}'
    return hashlib.md5(payload.encode()).hexdigest()


def cancel_payfast_subscription(token):
    """
    Cancel a recurring PayFast subscription by its token via the REST API.

    Returns True on success. When there is no token (e.g. a sandbox checkout
    that never minted a recurring token) there is nothing to cancel upstream, so
    we return True and let the caller do the local deactivation. In sandbox we
    skip the live call for the same reason. In production we POST/PUT the signed
    cancel and surface any network/HTTP error as False so the caller can report
    it rather than silently marking the plan cancelled.
    """
    if not token:
        return True

    if settings.PAYFAST_SANDBOX:
        logger.info(f'Sandbox: skipping PayFast API cancel for token {token}')
        return True

    headers = {
        'merchant-id': settings.PAYFAST_MERCHANT_ID,
        'version': 'v1',
        'timestamp': datetime.now(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
    }
    headers['signature'] = _api_signature(headers, settings.PAYFAST_PASSPHRASE)

    url = f'{settings.PAYFAST_GAS_GUARD_API_URL}/subscriptions/{token}/cancel?testing=false'
    request = Request(url, data=b'', method='PUT', headers=headers)
    try:
        with urlopen(request, timeout=20) as resp:
            body = resp.read().decode('utf-8', 'replace')
            logger.info(f'PayFast cancel OK for token {token}: {body}')
            return True
    except HTTPError as e:
        detail = e.read().decode('utf-8', 'replace') if e.fp else ''
        logger.error(f'PayFast cancel HTTP {e.code} for token {token}: {detail}')
        return False
    except (URLError, OSError) as e:
        logger.error(f'PayFast cancel network error for token {token}: {e}')
        return False
