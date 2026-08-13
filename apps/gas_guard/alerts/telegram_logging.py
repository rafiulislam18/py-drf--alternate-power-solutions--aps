"""
Ship WARNING+ log records to a Telegram chat, so failures surface in real time
without anyone tailing a log file.

This does NOT replace the file/console handlers — every record still lands in
prod.log exactly as before. Telegram is an extra, best-effort channel:

  * Threshold is set on the handler in LOGGING (WARNING by default), so INFO/DEBUG
    never reach here.
  * There is deliberately NO throttling or dedupe. If Telegram gets noisy (or hits
    its own rate limit), prod.log is the complete, authoritative record.
  * Sending is off-thread via Celery, and every failure is swallowed. A log call
    must never block a request or raise — if the broker/Telegram is down, the
    record is simply not delivered here (it's still in prod.log).

Config (settings.py / .env):
    ALERT_TELEGRAM_LOGS_ENABLED   default False
    TELEGRAM_LOG_BOT_TOKEN        dedicated logs bot (falls back to TELEGRAM_BOT_TOKEN)
    TELEGRAM_LOG_CHAT_ID          the logs chat/group (falls back to TELEGRAM_CHAT_ID)
    TELEGRAM_LOG_TIMEOUT          per-request HTTP timeout in seconds (default 15)
"""

import html
import logging

import requests
from celery import shared_task
from django.conf import settings

# Plain module logger for this module's OWN diagnostics. It must NOT carry the
# Telegram handler, or a Telegram delivery failure logged here could recurse.
logger = logging.getLogger(__name__)

_LEVEL_EMOJI = {
    'WARNING': '🟡',
    'ERROR': '🔴',
    'CRITICAL': '🚨',
}


def _telegram_log_token():
    """Prefer the dedicated logs bot; fall back to a general bot if present."""
    return getattr(settings, 'TELEGRAM_LOG_BOT_TOKEN', None) or getattr(
        settings, 'TELEGRAM_BOT_TOKEN', None
    )


def _telegram_log_chat_id():
    """Prefer a dedicated logs chat; fall back to a general chat if present."""
    return getattr(settings, 'TELEGRAM_LOG_CHAT_ID', None) or getattr(
        settings, 'TELEGRAM_CHAT_ID', None
    )


@shared_task
def send_telegram_log_task(text):
    """
    Deliver one preformatted log message to the Telegram logs chat.

    Runs off the request/worker thread. Any failure is logged (to file only) and
    swallowed — never retried, never raised — because a dropped log line is not
    worth stacking on the broker or masking the original error.
    """
    if not getattr(settings, 'ALERT_TELEGRAM_LOGS_ENABLED', False):
        return False

    token = _telegram_log_token()
    chat_id = _telegram_log_chat_id()
    if not token or not chat_id:
        logger.warning(
            "Telegram log skipped: TELEGRAM_LOG_BOT_TOKEN (or TELEGRAM_BOT_TOKEN) "
            "/ TELEGRAM_LOG_CHAT_ID (or TELEGRAM_CHAT_ID) not configured."
        )
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            },
            timeout=getattr(settings, 'TELEGRAM_LOG_TIMEOUT', 15),
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        # File-only logger, so this can't recurse back into Telegram.
        logger.error("Telegram log delivery failed: %s", exc)
        return False


class TelegramLogHandler(logging.Handler):
    """
    A logging handler that forwards records to Telegram via a Celery task.

    The level threshold is configured in the LOGGING dict (see settings.py), so
    this handler only ever sees records at/above that level. emit() itself never
    raises: it formats the record and hands off to Celery, catching everything.
    """

    def emit(self, record):
        try:
            text = self._format_record(record)
            # Off-thread hand-off. If the broker is unreachable, .delay() may
            # raise — that's caught below and the record is dropped here (still
            # in prod.log). We never fall through to a blocking synchronous send.
            send_telegram_log_task.delay(text)
        except Exception:
            # Last-resort guard. self.handleError writes to stderr, never to a
            # logger, so it cannot recurse into this handler.
            self.handleError(record)

    def _format_record(self, record):
        emoji = _LEVEL_EMOJI.get(record.levelname, '⚪')
        # html.escape everything user/record-derived — parse_mode is HTML.
        message = html.escape(record.getMessage())
        location = html.escape(f"{record.pathname}:{record.lineno}")
        name = html.escape(record.name)

        lines = [
            f"{emoji} <b>{html.escape(record.levelname)}</b> — <code>{name}</code>",
            "",
            message,
            "",
            f"<i>{location}</i>",
        ]

        if record.exc_info:
            tb = html.escape(self.format(record))
            # Telegram hard-caps a message at 4096 chars; keep headroom.
            if len(tb) > 3500:
                tb = tb[:3500] + "\n… (truncated — see prod.log)"
            lines.append(f"<pre>{tb}</pre>")

        return "\n".join(lines)
