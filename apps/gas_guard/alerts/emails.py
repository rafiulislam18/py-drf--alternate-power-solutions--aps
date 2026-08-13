"""
Low-gas alert email delivery.

A single branded HTML email is sent to both the site owner (the client) and the
APS admin inbox. Failures are logged and swallowed — an alert that fails to
send must never crash the hourly monitoring task.
"""

import html
import logging

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)

# Gas Guard brand colours (matched to the dashboard theme).
BG = '#f8f9fa'
INK = '#0c0e12'
CYAN = '#1FA463'          # "ok" green, used for recovery
WARN = '#E8A300'          # low-gas amber
ALARM = '#E23A2E'         # urgent red


def _email_shell(accent, badge, title, body_html):
    """Shared branded email wrapper for all Gas Guard alert emails."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head><style>body {{ font-family: Arial, sans-serif; line-height: 1.6; }}</style></head>
    <body>
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: {BG}; border-radius: 10px;">
            <h2 style="color: {INK}; text-align: center; border-bottom: 2px solid {accent}; padding-bottom: 10px;">
                Gas Guard Monitoring Alert
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
                <p>This is an automated message from Gas Guard — a product of APS.</p>
            </div>
        </div>
    </body>
    </html>
    """


def _details_block(context):
    """The shared site/reading detail card used in both fault and recovery mails."""
    site = html.escape(str(context.get('site_name', 'a site')))
    pct = context.get('pct')
    gas_kg = context.get('gas_kg')
    threshold = context.get('threshold')
    est_days = context.get('est_days')
    checked_at = html.escape(str(context.get('checked_at', '')))

    pct_txt = f'{pct}%' if pct is not None else 'n/a'
    gas_txt = f'{gas_kg:.1f} kg' if isinstance(gas_kg, (int, float)) else 'n/a'
    est_txt = f'{est_days} days' if est_days is not None else 'n/a'

    return f"""
                <div style="margin-top:16px; padding:12px 15px; background-color:{BG}; border-left:4px solid {INK};">
                    <p style="margin:4px 0;"><strong>Site:</strong> {site}</p>
                    <p style="margin:4px 0;"><strong>Gas remaining:</strong> {pct_txt} ({gas_txt})</p>
                    <p style="margin:4px 0;"><strong>Alert threshold:</strong> {threshold}%</p>
                    <p style="margin:4px 0;"><strong>Estimated days left:</strong> {est_txt}</p>
                    <p style="margin:4px 0;"><strong>Checked at:</strong> {checked_at}</p>
                </div>
    """


def _build_email_html(context):
    """Render the low-gas or recovery email body from the alert context."""
    is_fault = context['is_fault']
    site = html.escape(str(context.get('site_name', 'a site')))
    pct = context.get('pct')
    threshold = context.get('threshold')

    if is_fault:
        accent = ALARM if (pct is not None and pct <= 12) else WARN
        badge = 'LOW GAS'
        title = f'{site} is running low'
        intro = (
            f"The cylinder at <strong>{site}</strong> has dropped to "
            f"<strong>{pct}%</strong> remaining, at or below the {threshold}% "
            f"alert threshold. Arrange a refill before it runs out."
        )
    else:
        accent = CYAN
        badge = 'REFILLED'
        title = f'{site} is topped up'
        intro = (
            f"Good news — the cylinder at <strong>{site}</strong> is back up to "
            f"<strong>{pct}%</strong>, above the {threshold}% threshold. "
            f"This low-gas alert has cleared."
        )

    body = f'<p style="color:#333;">{intro}</p>{_details_block(context)}'
    return _email_shell(accent, badge, title, body)


def send_low_gas_email(context, recipients):
    """
    Send the branded low-gas / recovery email to every recipient.

    `recipients` is the deduped list of client + admin addresses. Returns True
    if the message was accepted by the SMTP server, False otherwise.
    """
    if not getattr(settings, 'ALERT_EMAIL_ENABLED', False):
        logger.info('ALERT_EMAIL_ENABLED is off — skipping low-gas email.')
        return False

    sender = settings.EMAIL_HOST_USER
    recipients = [r for r in dict.fromkeys(recipients) if r]  # dedupe, drop blanks
    if not sender or not recipients:
        logger.warning(
            'Low-gas email skipped: EMAIL_HOST_USER or recipients not configured.'
        )
        return False

    is_fault = context['is_fault']
    site = context.get('site_name', 'a site')
    subject = (
        f'Gas Guard: {site} is low on gas'
        if is_fault
        else f'Gas Guard: {site} refilled'
    )
    try:
        email = EmailMessage(
            subject=subject,
            body=_build_email_html(context),
            from_email=sender,
            to=recipients,
        )
        email.content_subtype = 'html'
        email.send(fail_silently=False)
        logger.info('Low-gas email sent to %s', recipients)
        return True
    except Exception as exc:
        logger.error('Low-gas email failed: %s', exc)
        return False
