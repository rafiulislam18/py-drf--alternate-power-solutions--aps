"""
Registration email — the branded, Gas Guard dark-theme verification code.

Kept out of the views so the (long) HTML doesn't drown the request logic, and
so register + resend share exactly one template. Mirrors the email convention
used elsewhere (Gmail SMTP, EmailMessage with content_subtype='html',
fail_silently handled by the caller).
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)

# How long a verification code stays valid, in minutes. Referenced in the copy.
CODE_TTL_MINUTES = 15


def send_verification_email(email, code, first_name=''):
    """
    Email a 6-digit verification code to a pending registrant.

    Returns True if the send succeeded, False otherwise. Raises nothing —
    the caller decides what a failure means (register rolls the pending row
    back; resend just reports the error).
    """
    sender = settings.EMAIL_HOST_USER
    if not sender:
        logger.warning('EMAIL_HOST_USER not set — cannot send verification email.')
        return False

    greeting = f'Hi {first_name},' if first_name else 'Hi there,'
    html_message = _verification_email_html(code, greeting)
    try:
        message = EmailMessage(
            subject='Verify your email — Gas Guard',
            body=html_message,
            from_email=sender,
            to=[email],
        )
        message.content_subtype = 'html'
        message.send(fail_silently=False)
        logger.info(f'Verification code emailed to {email}')
        return True
    except Exception as e:
        logger.error(f'Error sending verification email to {email}: {e}')
        return False


def _verification_email_html(code, greeting):
    """Gas Guard-branded verification email (dark theme: cyan + amber)."""
    return f"""\
<div style="font-family: Arial, Helvetica, sans-serif; max-width: 560px; margin: 0 auto;
            background: #0c0e12; border-radius: 16px; overflow: hidden;
            border: 1px solid #1c2029;">
  <div style="background: #0c0e12; padding: 28px 28px 20px; text-align: center;
              border-bottom: 1px solid #1c2029;">
    <div style="font-size: 22px; font-weight: 800; letter-spacing: -.5px; color: #fff;">
      Gas <span style="color: #27d3e0;">Guard</span>
    </div>
    <div style="margin-top: 6px; font-size: 12px; color: #9aa4b2;
                text-transform: uppercase; letter-spacing: 1.5px;">
      Verify your email
    </div>
  </div>

  <div style="padding: 28px;">
    <p style="font-size: 15px; color: #e6e9ef; margin: 0 0 14px;">{greeting}</p>
    <p style="font-size: 14px; color: #9aa4b2; margin: 0 0 22px; line-height: 1.6;">
      Thanks for signing up to Gas Guard. Enter the code below to confirm your
      email address and activate your account.
    </p>

    <div style="background: #12151b; border: 1px solid #1c2029;
                border-left: 4px solid #27d3e0; border-radius: 10px;
                padding: 22px; text-align: center; margin: 0 0 22px;">
      <div style="font-family: 'Courier New', monospace; font-size: 34px;
                  font-weight: 800; letter-spacing: 8px; color: #27d3e0;">{code}</div>
      <div style="margin-top: 8px; font-size: 12px; color: #9aa4b2;">
        Enter this code to verify your email
      </div>
    </div>

    <div style="background: #1a1408; border-left: 4px solid #f0a63a;
                border-radius: 8px; padding: 14px 16px;">
      <p style="margin: 0; font-size: 13px; color: #f0a63a;">
        This code expires in {CODE_TTL_MINUTES} minutes. If you didn't request it,
        you can safely ignore this email.
      </p>
    </div>
  </div>

  <div style="background: #0a0c10; padding: 16px 28px; text-align: center;
              border-top: 1px solid #1c2029;">
    <p style="margin: 0; font-size: 12px; color: #6b7280;">
      Gas Guard &mdash; smart LPG monitoring, a product of APS
    </p>
  </div>
</div>
"""
