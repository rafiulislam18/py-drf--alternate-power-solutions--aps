"""Account emails: verify address, reset password, claim a subscriber account."""

from urllib.parse import quote

from django.conf import settings
from django.utils.html import escape

from apps.core.mail import button_html, send_html, wrap_html

from .tokens import make_claim_token, make_reset_token, make_verify_token

_HELP = (
    '<p style="color: #666; font-size: 13px; margin: 16px 0 0;">'
    'If you didn\'t ask for this, you can ignore this email — nothing changes on your account.</p>'
)


def _link(path, token):
    return f'{settings.FRONTEND_BASE_URL}{path}?token={quote(token)}'


def _greeting(user):
    try:
        name = user.client_profile.company_name
    except Exception:  # noqa: BLE001 — staff accounts have no profile
        name = ''
    return f'<p style="margin: 0 0 12px;">Hi {escape(name or user.get_username())},</p>'


def send_verification_email(user, email=None):
    """Ask the owner of ``email`` (default: the account email) to confirm it."""
    email = email or user.email
    url = _link('/dashboard/verify-email', make_verify_token(user, email))
    body = f"""
        {_greeting(user)}
        <p style="margin: 0 0 12px;">Please confirm that <strong>{escape(email)}</strong> is your email address
        for the APS dashboard. Once it's confirmed you can sign in with it.</p>
        {button_html(url, 'Confirm my email')}
        <p style="color: #666; font-size: 13px; margin: 16px 0 0;">This link works for 3 days.</p>
        {_HELP}
    """
    return send_html('Confirm your email for the APS dashboard', wrap_html('Confirm Your Email', body), [email],
                     log_prefix='Accounts')


def send_password_reset_email(user):
    url = _link('/dashboard/reset-password', make_reset_token(user))
    username = ''
    if user.get_username().lower() != (user.email or '').lower():
        username = (f'<p style="margin: 0 0 12px;">Your username is <strong>{escape(user.get_username())}</strong> '
                    f'— you can sign in with it or with this email.</p>')
    body = f"""
        {_greeting(user)}
        <p style="margin: 0 0 12px;">We received a request to reset the password for your APS dashboard account.</p>
        {username}
        {button_html(url, 'Choose a new password')}
        <p style="color: #666; font-size: 13px; margin: 16px 0 0;">This link works for 1 hour and only once.</p>
        {_HELP}
    """
    return send_html('Reset your APS dashboard password', wrap_html('Reset Your Password', body), [user.email],
                     log_prefix='Accounts')


def send_claim_account_email(email):
    """A subscriber with no dashboard login asked to reset: offer to create one."""
    url = _link('/dashboard/reset-password', make_claim_token(email))
    body = f"""
        <p style="margin: 0 0 12px;">Hi,</p>
        <p style="margin: 0 0 12px;">You have an APS subscription under <strong>{escape(email)}</strong>, and you can now
        manage it from the APS dashboard. Choose a password to set up your account — you'll then see your
        subscriptions and payment history, and can log service tickets for your sites.</p>
        {button_html(url, 'Set up my account')}
        <p style="color: #666; font-size: 13px; margin: 16px 0 0;">This link works for 1 hour.</p>
        {_HELP}
    """
    return send_html('Set up your APS dashboard account', wrap_html('Your APS Account', body), [email],
                     log_prefix='Accounts')


def send_account_exists_email(user):
    """Someone signed up with an address that already has an account."""
    login_url = f'{settings.FRONTEND_BASE_URL}/dashboard/login'
    forgot_url = f'{settings.FRONTEND_BASE_URL}/dashboard/forgot-password'
    body = f"""
        {_greeting(user)}
        <p style="margin: 0 0 12px;">Someone tried to create an APS dashboard account with <strong>{escape(user.email)}</strong>,
        but you already have one.</p>
        {button_html(login_url, 'Sign in')}
        <p style="margin: 16px 0 0;">Forgotten your password?
        <a href="{escape(forgot_url)}" style="color: #D96F32;">Reset it here</a>.</p>
        {_HELP}
    """
    return send_html('You already have an APS dashboard account', wrap_html('Your APS Account', body), [user.email],
                     log_prefix='Accounts')
