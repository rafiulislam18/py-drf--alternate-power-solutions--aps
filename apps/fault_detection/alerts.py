"""
Alert delivery for fault detection.

Each channel is independent and guarded by its own config flag, so a
misconfigured or disabled channel never blocks the others. Failures are logged
and swallowed — an alert that fails to send must not crash the monitoring task.
"""

import logging

import requests
from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)


def send_email_alert(subject, body):
    """Send a plain-text alert email to EMAIL_RECIPIENT (project convention)."""
    if not getattr(settings, 'ALERT_EMAIL_ENABLED', False):
        return False
    recipient = settings.EMAIL_RECIPIENT
    sender = settings.EMAIL_HOST_USER
    if not recipient or not sender:
        logger.warning("Email alert skipped: EMAIL_RECIPIENT / EMAIL_HOST_USER not configured.")
        return False
    try:
        email = EmailMessage(
            subject=subject,
            body=body,
            from_email=sender,
            to=[recipient],
        )
        email.send(fail_silently=False)
        logger.info("Email alert sent to %s", recipient)
        return True
    except Exception as exc:
        logger.error("Email alert failed: %s", exc)
        return False


def send_telegram_alert(body):
    """Send an alert to a Telegram chat via the Bot API."""
    if not getattr(settings, 'ALERT_TELEGRAM_ENABLED', False):
        return False
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.warning("Telegram alert skipped: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={'chat_id': chat_id, 'text': body, 'disable_web_page_preview': True},
            timeout=getattr(settings, 'HA_REQUEST_TIMEOUT', 15),
        )
        resp.raise_for_status()
        logger.info("Telegram alert sent to chat %s", chat_id)
        return True
    except Exception as exc:
        logger.error("Telegram alert failed: %s", exc)
        return False


def dispatch_alert(subject, body):
    """
    Send an alert over all enabled channels. Returns a dict of per-channel
    success so the caller can log what actually went out.
    """
    return {
        'email': send_email_alert(subject, body),
        'telegram': send_telegram_alert(body),
    }
