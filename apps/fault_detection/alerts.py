"""
Alert delivery for fault detection.

Each channel is independent and guarded by its own config flag, so a
misconfigured or disabled channel never blocks the others. Failures are logged
and swallowed — an alert that fails to send must not crash the monitoring task.

Messages are built from a structured `context` dict so each channel can render
its own format: a branded HTML email matching the rest of the APS site, and a
formatted Telegram (HTML) message.
"""

import html
import logging

import requests
from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)

# APS brand colours (matched to the other site email templates).
BRAND = '#D96F32'
BG = '#f8f9fa'
OK_GREEN = '#2e7d32'
ALERT_RED = '#c0392b'


def _friendly(entity_id):
    """Turn 'sensor.ies_ies_3_soc' into a short label like 'IES 3'."""
    name = entity_id.split('.')[-1]
    name = name.replace('ies_ies_', 'ies_').replace('_soc', '')
    return name.replace('ies_', 'IES ').replace('_', ' ').strip().upper().replace('IES ', 'IES ')


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def _build_email_html(context):
    """Render the branded HTML email body from the alert context."""
    is_fault = context['is_fault']
    spread = context['spread']
    threshold = context['threshold']
    readings = context['readings']  # list of (entity_id, soc)
    min_entity, min_soc = context['min']
    max_entity, max_soc = context['max']
    checked_at = context['checked_at']

    accent = ALERT_RED if is_fault else OK_GREEN
    title = 'SOC Imbalance Detected' if is_fault else 'SOC Imbalance Cleared'
    badge = 'FAULT' if is_fault else 'RECOVERED'
    intro = (
        f"A battery State-of-Charge imbalance has been detected across the IES banks. "
        f"The spread between the highest and lowest bank is <strong>{spread:.2f}%</strong>, "
        f"which exceeds the alert threshold of {threshold:.2f}%."
        if is_fault else
        f"The previously detected SOC imbalance has cleared. The spread is now "
        f"<strong>{spread:.2f}%</strong>, back within the {threshold:.2f}% threshold."
    )

    # Per-bank table rows; highlight the min and max banks.
    rows = ""
    for entity_id, soc in readings:
        label = _friendly(entity_id)
        highlight = ""
        tag = ""
        if entity_id == min_entity:
            highlight = "background-color: #fdecea;"
            tag = ' <span style="color:%s; font-weight:bold;">(lowest)</span>' % ALERT_RED
        elif entity_id == max_entity:
            highlight = "background-color: #eafaf1;"
            tag = ' <span style="color:%s; font-weight:bold;">(highest)</span>' % OK_GREEN
        rows += (
            f'<tr style="{highlight}">'
            f'<td style="padding:8px 12px; border-bottom:1px solid #eee;">{html.escape(label)}{tag}</td>'
            f'<td style="padding:8px 12px; border-bottom:1px solid #eee; text-align:right; font-weight:bold;">{soc:.2f}%</td>'
            f'</tr>'
        )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
        </style>
    </head>
    <body>
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: {BG}; border-radius: 10px;">
            <h2 style="color: {BRAND}; text-align: center; border-bottom: 2px solid {BRAND}; padding-bottom: 10px;">
                Solar Monitoring Alert
            </h2>
            <div style="text-align:center; margin: 16px 0;">
                <span style="display:inline-block; background-color:{accent}; color:#fff; padding:6px 16px; border-radius:20px; font-weight:bold; letter-spacing:1px;">
                    {badge}
                </span>
            </div>
            <div style="background-color: white; padding: 20px; border-radius: 5px; margin-top: 10px;">
                <h3 style="color:{accent}; margin-top:0;">{title}</h3>
                <p style="color:#333;">{intro}</p>

                <table style="width:100%; border-collapse:collapse; margin-top:16px;">
                    <thead>
                        <tr>
                            <th style="text-align:left; padding:8px 12px; border-bottom:2px solid {BRAND}; color:{BRAND};">Battery Bank</th>
                            <th style="text-align:right; padding:8px 12px; border-bottom:2px solid {BRAND}; color:{BRAND};">State of Charge</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>

                <div style="margin-top:16px; padding:12px 15px; background-color:{BG}; border-left:4px solid {BRAND};">
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Spread (max − min):</strong> {spread:.2f}%</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Alert threshold:</strong> {threshold:.2f}%</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Lowest:</strong> {_friendly(min_entity)} ({min_soc:.2f}%)</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Highest:</strong> {_friendly(max_entity)} ({max_soc:.2f}%)</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Checked at:</strong> {checked_at}</p>
                </div>
            </div>
            <div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
                <p>This is an automated message from Alternate Power Solutions</p>
            </div>
        </div>
    </body>
    </html>
    """


def _build_telegram_html(context):
    """Render a formatted Telegram message (Telegram HTML parse mode)."""
    is_fault = context['is_fault']
    spread = context['spread']
    threshold = context['threshold']
    readings = context['readings']
    min_entity, min_soc = context['min']
    max_entity, max_soc = context['max']
    checked_at = context['checked_at']

    header = (
        "🔴 <b>SOC IMBALANCE DETECTED</b>" if is_fault
        else "🟢 <b>SOC IMBALANCE CLEARED</b>"
    )
    lines = [header, ""]
    if is_fault:
        lines.append(f"Spread <b>{spread:.2f}%</b> exceeds threshold {threshold:.2f}%.")
    else:
        lines.append(f"Spread back to <b>{spread:.2f}%</b>, within {threshold:.2f}%.")
    lines.append("")
    lines.append("<b>Battery banks:</b>")
    for entity_id, soc in readings:
        marker = ""
        if entity_id == min_entity:
            marker = "  ⬇️ lowest"
        elif entity_id == max_entity:
            marker = "  ⬆️ highest"
        lines.append(f"• {html.escape(_friendly(entity_id))}: <b>{soc:.2f}%</b>{marker}")
    lines.append("")
    lines.append(f"<i>Checked at {html.escape(str(checked_at))}</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Channel senders
# ---------------------------------------------------------------------------

def send_email_alert(subject, context):
    """Send a branded HTML alert email to EMAIL_RECIPIENT (project convention)."""
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
            body=_build_email_html(context),
            from_email=sender,
            to=[recipient],
        )
        email.content_subtype = "html"
        email.send(fail_silently=False)
        logger.info("Email alert sent to %s", recipient)
        return True
    except Exception as exc:
        logger.error("Email alert failed: %s", exc)
        return False


def send_telegram_alert(context):
    """Send a formatted alert to a Telegram chat via the Bot API."""
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
            json={
                'chat_id': chat_id,
                'text': _build_telegram_html(context),
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            },
            timeout=getattr(settings, 'HA_REQUEST_TIMEOUT', 15),
        )
        resp.raise_for_status()
        logger.info("Telegram alert sent to chat %s", chat_id)
        return True
    except Exception as exc:
        logger.error("Telegram alert failed: %s", exc)
        return False


def dispatch_alert(subject, context):
    """
    Send an alert over all enabled channels using the structured context.
    Returns a dict of per-channel success so the caller can log what went out.
    """
    return {
        'email': send_email_alert(subject, context),
        'telegram': send_telegram_alert(context),
    }
