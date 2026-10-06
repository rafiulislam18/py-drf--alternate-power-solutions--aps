"""
Themed HTML emails for the dashboard (accounts, tickets).

Same SMTP path and look as the rest of the APS site — accent ``#D96F32`` on a
``#f8f9fa`` panel with a white card (see ``apps.subscription_portal.emails``).
Callers HTML-escape anything a user typed. Failures are logged, never raised:
a saved ticket or account must not turn into an error because SMTP hiccupped.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils.html import escape

logger = logging.getLogger('apps.core.mail')

ACCENT = '#D96F32'


def wrap_html(heading, body_html):
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
        </style>
    </head>
    <body>
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa; border-radius: 10px;">
            <h2 style="color: {ACCENT}; text-align: center; border-bottom: 2px solid {ACCENT}; padding-bottom: 10px;">
                {heading}
            </h2>
            <div style="background-color: white; padding: 20px; border-radius: 5px; margin-top: 20px;">
                {body_html}
            </div>
            <div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
                <p>This is an automated message from Alternate Power Solutions</p>
            </div>
        </div>
    </body>
    </html>
    """


def button_html(url, label):
    """A centred call-to-action button plus the raw link for clients that strip buttons."""
    safe = escape(url)
    return f"""
        <p style="text-align: center; margin: 24px 0;">
            <a href="{safe}" style="display: inline-block; background: {ACCENT}; color: #ffffff; text-decoration: none;
               font-weight: bold; padding: 12px 24px; border-radius: 6px;">{escape(label)}</a>
        </p>
        <p style="color: #666; font-size: 12px; margin: 0; word-break: break-all;">
            Or paste this link into your browser: <a href="{safe}" style="color: {ACCENT};">{safe}</a>
        </p>
    """


def send_html(subject, html, to, log_prefix='Dashboard'):
    # A CR/LF in a header (e.g. a ticket title) makes Django raise
    # BadHeaderError and the email silently never goes; flatten it instead.
    subject = ' '.join(str(subject).split())
    recipients = [addr for addr in to if addr]
    if not recipients:
        logger.warning(f'{log_prefix} email "{subject}" skipped: no recipient configured.')
        return False
    try:
        message = EmailMessage(
            subject=subject,
            body=html,
            from_email=settings.EMAIL_HOST_USER,
            to=recipients,
        )
        message.content_subtype = 'html'
        message.send(fail_silently=False)
        return True
    except Exception as exc:  # noqa: BLE001 — email must never break the request
        logger.error(f'{log_prefix} email "{subject}" to {recipients} failed: {exc}')
        return False
