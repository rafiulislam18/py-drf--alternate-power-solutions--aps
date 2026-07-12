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
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)

# APS brand colours (matched to the other site email templates).
BRAND = '#D96F32'
BG = '#f8f9fa'
OK_GREEN = '#2e7d32'
ALERT_RED = '#c0392b'


def _pickup_url(context):
    """Build the frontend pickup-page URL for an alert, or '' if none is attached.

    tasks.py attaches the FaultAlert's uuid as context['alert_uuid'] when it
    creates the record on the rising edge. Recovery/behaviour-clear alerts have
    no pickup, so this returns '' and the CTA is omitted.
    """
    alert_uuid = context.get('alert_uuid')
    if not alert_uuid:
        return ''
    base = settings.FRONTEND_BASE_URL
    return f"{base}/fault-detection/pickup/{alert_uuid}/"


def _pickup_cta_html(url):
    """A branded 'confirm pickup' button + helper text for the fault email."""
    if not url:
        return ''
    return f"""
                <div style="text-align:center; margin:24px 0 8px;">
                    <a href="{html.escape(url)}"
                       style="display:inline-block; background-color:{BRAND}; color:#ffffff;
                              text-decoration:none; padding:12px 28px; border-radius:24px;
                              font-weight:bold; letter-spacing:0.5px;">
                        Confirm Pickup
                    </a>
                </div>
                <p style="text-align:center; color:#666; font-size:13px; margin:4px 0 0;">
                    Are you picking up this alert? Please click the button above and enter your
                    name so the team knows it's being handled.
                </p>
    """


def _pickup_cta_telegram(url):
    """Helper lines prompting the responder to confirm pickup via the link."""
    if not url:
        return []
    return [
        "",
        "\U0001F449 <b>Are you picking this up?</b>",
        f'Please confirm here so the team knows it\'s handled:\n{html.escape(url)}',
    ]


def _friendly(entity_id):
    """Turn 'sensor.ies_ies_3_soc' into a short label like 'IES 3'."""
    name = entity_id.split('.')[-1]
    name = name.replace('ies_ies_', 'ies_').replace('_soc', '')
    return name.replace('ies_', 'IES ').replace('_', ' ').strip().upper().replace('IES ', 'IES ')


# ---------------------------------------------------------------------------
# Message builders — dispatch by context['kind']
# ---------------------------------------------------------------------------

def _build_email_html(context):
    """Route to the right email body based on the alert kind."""
    if context.get('kind') == 'pickup_confirmation':
        return _build_pickup_email_html(context)
    if context.get('kind') == 'charge_behaviour':
        return _build_behaviour_email_html(context)
    return _build_soc_email_html(context)


def _build_telegram_html(context):
    """Route to the right Telegram body based on the alert kind."""
    if context.get('kind') == 'pickup_confirmation':
        return _build_pickup_telegram_html(context)
    if context.get('kind') == 'charge_behaviour':
        return _build_behaviour_telegram_html(context)
    return _build_soc_telegram_html(context)


def _email_shell(accent, badge, title, body_html):
    """Shared branded email wrapper used by all alert kinds."""
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
                {body_html}
            </div>
            <div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
                <p>This is an automated message from Alternate Power Solutions</p>
            </div>
        </div>
    </body>
    </html>
    """


def _build_soc_email_html(context):
    """Render the branded HTML email body from the SOC imbalance context."""
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

    body = f"""
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
                {_pickup_cta_html(_pickup_url(context)) if is_fault else ''}
    """
    return _email_shell(accent, badge, title, body)


def _build_soc_telegram_html(context):
    """Render a formatted Telegram message for SOC imbalance (HTML parse mode)."""
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
    if is_fault:
        lines.extend(_pickup_cta_telegram(_pickup_url(context)))
    return "\n".join(lines)


def _build_behaviour_email_html(context):
    """Render the branded HTML email body for the charge/discharge behaviour alert."""
    is_fault = context['is_fault']
    mode = context.get('mode', '?')
    expected = context.get('expected', '?')
    actual = context.get('actual', '?')
    power = context.get('power')
    soc = context.get('soc')
    checked_at = context.get('checked_at', '')

    accent = ALERT_RED if is_fault else OK_GREEN
    title = 'Battery Not Following Schedule' if is_fault else 'Battery Back On Schedule'
    badge = 'FAULT' if is_fault else 'RECOVERED'
    mode_h = html.escape(str(mode).title())
    if is_fault:
        intro = (
            f"This is a <strong>{mode_h}</strong> period in the APS schedule, so the battery "
            f"should be <strong>{html.escape(expected)}</strong>, but it is currently "
            f"<strong>{html.escape(actual)}</strong>. The battery is not following the APS schedule."
        )
    else:
        intro = (
            f"The battery is following the APS schedule again — during this <strong>{mode_h}</strong> "
            f"period it is correctly <strong>{html.escape(actual)}</strong>."
        )

    power_txt = f"{power:.1f} kW" if isinstance(power, (int, float)) else "n/a"
    soc_txt = f"{soc:.0f}%" if isinstance(soc, (int, float)) else "n/a"

    body = f"""
                <p style="color:#333;">{intro}</p>
                <div style="margin-top:16px; padding:12px 15px; background-color:{BG}; border-left:4px solid {BRAND};">
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Scheduled period:</strong> {mode_h}</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Expected:</strong> {html.escape(expected)}</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Actual:</strong> {html.escape(actual)}</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Battery power:</strong> {power_txt}</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Battery SoC:</strong> {soc_txt}</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Checked at:</strong> {html.escape(str(checked_at))}</p>
                </div>
                {_pickup_cta_html(_pickup_url(context)) if is_fault else ''}
    """
    return _email_shell(accent, badge, title, body)


def _build_behaviour_telegram_html(context):
    """Render a formatted Telegram message for the charge/discharge behaviour alert."""
    is_fault = context['is_fault']
    mode = str(context.get('mode', '?')).title()
    expected = context.get('expected', '?')
    actual = context.get('actual', '?')
    power = context.get('power')
    soc = context.get('soc')
    checked_at = context.get('checked_at', '')

    power_txt = f"{power:.1f} kW" if isinstance(power, (int, float)) else "n/a"
    soc_txt = f"{soc:.0f}%" if isinstance(soc, (int, float)) else "n/a"

    if is_fault:
        header = "🔴 <b>BATTERY NOT FOLLOWING SCHEDULE</b>"
        summary = (
            f"APS schedule says <b>{html.escape(mode)}</b>, so the battery should be "
            f"<b>{html.escape(expected)}</b>, but it is <b>{html.escape(actual)}</b>."
        )
    else:
        header = "🟢 <b>BATTERY BACK ON SCHEDULE</b>"
        summary = f"In the <b>{html.escape(mode)}</b> period the battery is correctly <b>{html.escape(actual)}</b>."

    lines = [
        header, "", summary, "",
        f"• Scheduled period: <b>{html.escape(mode)}</b>",
        f"• Battery power: <b>{power_txt}</b>",
        f"• Battery SoC: <b>{soc_txt}</b>",
        "",
        f"<i>Checked at {html.escape(str(checked_at))}</i>",
    ]
    if is_fault:
        lines.extend(_pickup_cta_telegram(_pickup_url(context)))
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


# ---------------------------------------------------------------------------
# Pickup confirmation ("X has picked up this alert")
# ---------------------------------------------------------------------------

def _build_pickup_email_html(context):
    """Branded email confirming who picked up a fault alert."""
    who = html.escape(str(context.get('picked_up_by', 'Someone')))
    fault = html.escape(str(context.get('fault_label', 'a fault alert')))
    picked_at = html.escape(str(context.get('picked_up_at', '')))
    summary = context.get('summary', '')

    body = f"""
                <p style="color:#333;">
                    <strong>{who}</strong> has confirmed pickup of the following alert and is
                    now handling it.
                </p>
                <div style="margin-top:16px; padding:12px 15px; background-color:{BG}; border-left:4px solid {OK_GREEN};">
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Alert:</strong> {fault}</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Picked up by:</strong> {who}</p>
                    <p style="margin:4px 0;"><strong style="color:{BRAND};">Picked up at:</strong> {picked_at}</p>
                </div>
    """
    if summary:
        body += (
            f'<p style="color:#888; font-size:12px; margin-top:16px;">Original alert: '
            f'{html.escape(str(summary))}</p>'
        )
    return _email_shell(OK_GREEN, 'PICKED UP', 'Alert Picked Up', body)


def _build_pickup_telegram_html(context):
    """Telegram message confirming who picked up a fault alert."""
    who = html.escape(str(context.get('picked_up_by', 'Someone')))
    fault = html.escape(str(context.get('fault_label', 'a fault alert')))
    picked_at = html.escape(str(context.get('picked_up_at', '')))
    return "\n".join([
        "✅ <b>ALERT PICKED UP</b>",
        "",
        f"<b>{who}</b> is now handling this alert:",
        f"• {fault}",
        "",
        f"<i>Picked up at {picked_at}</i>",
    ])


def dispatch_pickup_confirmation(alert):
    """
    Notify the team that `alert` has been claimed. Reuses the same channels the
    original fault went out on (email + Telegram). Sent once, when the claim
    succeeds. Delivery failures are logged and swallowed by the senders.
    """
    fault_label = alert.get_kind_display()
    # Project TIME_ZONE is UTC, so convert to the schedule timezone (SAST) for
    # display — consistent with how tasks.py stamps its timestamps.
    picked_at = ''
    if alert.picked_up_at:
        tz_name = getattr(settings, 'HA_SCHEDULE_TIMEZONE', None) or settings.CELERY_TIMEZONE
        picked_at = alert.picked_up_at.astimezone(ZoneInfo(tz_name)).strftime('%d/%m/%Y %H:%M')
    context = {
        'kind': 'pickup_confirmation',
        'picked_up_by': alert.picked_up_by,
        'picked_up_at': picked_at,
        'fault_label': fault_label,
        'summary': alert.summary,
    }
    return {
        'email': send_email_alert(f"APS Solar Alert Picked Up: {fault_label}", context),
        'telegram': send_telegram_alert(context),
    }
