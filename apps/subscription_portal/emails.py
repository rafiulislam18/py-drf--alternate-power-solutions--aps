"""
OTP email for the Manage-Subscriptions portal.

Uses the same SMTP path and visual theme as the rest of the APS site (see
``apps.quote_request`` — accent ``#D96F32`` on a ``#f8f9fa`` panel with a white
card and an "automated message from Alternate Power Solutions" footer), so the
code email looks like it belongs to the brand.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger('apps.subscription_portal')

_ACCENT = '#D96F32'


def _otp_email_html(code, minutes):
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
            <h2 style="color: {_ACCENT}; text-align: center; border-bottom: 2px solid {_ACCENT}; padding-bottom: 10px;">
                Manage Your Subscriptions
            </h2>
            <div style="background-color: white; padding: 20px; border-radius: 5px; margin-top: 20px; text-align: center;">
                <p style="margin: 0 0 12px;">Use this code to access your subscriptions:</p>
                <p style="font-size: 34px; font-weight: bold; letter-spacing: 8px; color: {_ACCENT}; margin: 10px 0;">
                    {code}
                </p>
                <p style="color: #666; font-size: 13px; margin: 12px 0 0;">
                    This code expires in {minutes} minutes. If you didn't request it, you can safely ignore this email.
                </p>
            </div>
            <div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
                <p>This is an automated message from Alternate Power Solutions</p>
            </div>
        </div>
    </body>
    </html>
    """


def send_otp_email(email, code, minutes):
    """Send the themed OTP email. Errors are logged, not raised, so the request
    still returns its neutral response even if SMTP hiccups."""
    try:
        message = EmailMessage(
            subject='Your APS subscription access code',
            body=_otp_email_html(code, minutes),
            from_email=settings.EMAIL_HOST_USER,
            to=[email],
        )
        message.content_subtype = 'html'
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 — never let email failure leak state
        logger.error(f'Failed to send portal OTP to {email}: {exc}')
