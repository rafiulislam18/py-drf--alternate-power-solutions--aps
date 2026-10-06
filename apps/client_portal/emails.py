"""
Ticket emails: new tickets (team + client confirmation — see alerts.py for
when they go out), the emergency Telegram ping, status updates, and the unread-chat digests (see digests.py).

Uses the shared dashboard email theme (``apps.core.mail``). Every value a
client typed is HTML-escaped before it goes into a message. Failures are
logged, never raised: a ticket that saved must not turn into an error because
SMTP hiccupped.
"""

import logging

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.html import escape, linebreaks

from apps.core.mail import ACCENT, send_html, wrap_html

logger = logging.getLogger('apps.client_portal')


def _profile(user):
    try:
        return user.client_profile
    except Exception:  # noqa: BLE001
        return None


def _client_name(ticket):
    profile = _profile(ticket.client)
    return (profile.company_name if profile else '') or ticket.client.get_username()


def _client_phone(ticket):
    profile = _profile(ticket.client)
    return profile.phone if profile else ''


def _ticket_url(ticket):
    return f'{settings.FRONTEND_BASE_URL}/dashboard/tickets/{ticket.pk}'


def client_can_be_emailed(ticket):
    """Ticket emails only ever go to a confirmed address."""
    profile = _profile(ticket.client)
    return bool(ticket.client.email and profile and profile.email_verified)


def _send(subject, html, to):
    return send_html(subject, html, to, log_prefix='Client ticket')


def _detail_rows(ticket):
    rows = [
        ('Ticket', ticket.reference),
        ('Client', _client_name(ticket)),
        ('Site', ticket.site.name + (f' — {ticket.site.address}' if ticket.site.address else '')),
        ('Service', ticket.service),
        ('Urgency', ticket.get_urgency_display()),
        ('Preferred visit', ticket.preferred_visit_date.strftime('%d/%m/%Y') if ticket.preferred_visit_date else '—'),
        ('Account', ' · '.join(filter(None, [ticket.client.email, _client_phone(ticket)])) or '—'),
        ('Contact on site', ' · '.join(filter(None, [ticket.site_contact_name, ticket.site_contact_phone])) or '—'),
        ('Attachments', str(ticket.attachments.count())),
    ]
    cells = ''.join(
        f'<tr><td style="padding: 4px 12px 4px 0; color: #666; white-space: nowrap; vertical-align: top;">{label}</td>'
        f'<td style="padding: 4px 0;">{escape(value)}</td></tr>'
        for label, value in rows
    )
    return f'<table style="border-collapse: collapse; font-size: 14px;">{cells}</table>'


# Longest problem description shown per ticket in a multi-ticket email.
_NEW_TICKETS_EXCERPT = 600


def _new_ticket_html(ticket, heading_tag='h3', excerpt=None):
    flag = ''
    if ticket.urgency == ticket.Urgency.EMERGENCY:
        flag = (
            '<p style="margin: 0 0 16px; padding: 10px 12px; background: #fdecea; color: #b3261e; '
            'border-radius: 4px; font-weight: bold;">EMERGENCY — the client expects a call now.</p>'
        )
    problem = ticket.description
    if excerpt and len(problem) > excerpt:
        problem = problem[:excerpt] + '…'
    return f"""
        {flag}
        <{heading_tag} style="margin: 0 0 12px;">{escape(ticket.title)}</{heading_tag}>
        {_detail_rows(ticket)}
        <h4 style="margin: 16px 0 6px;">Problem</h4>
        <div style="font-size: 14px;">{linebreaks(escape(problem))}</div>
        <p style="margin: 16px 0 0;"><a href="{escape(_ticket_url(ticket))}" style="color: {ACCENT};">Open the ticket in the dashboard</a></p>
    """


def notify_team_new_ticket(ticket):
    """Tell the APS team one ticket was raised (emergencies: at once; others via alerts.py)."""
    subject = f'[{ticket.get_urgency_display()}] New ticket {ticket.reference}: {ticket.title} ({_client_name(ticket)})'
    return _send(subject, wrap_html('New Client Ticket', _new_ticket_html(ticket)), [settings.EMAIL_RECIPIENT])


def notify_team_new_tickets(tickets):
    """The 30-minute roundup: one email for every ticket raised since the last run."""
    if len(tickets) == 1:
        return notify_team_new_ticket(tickets[0])
    urgent = sum(1 for t in tickets if t.urgency == t.Urgency.URGENT)
    blocks = ''.join(
        f'<div style="margin: 0 0 28px; padding-top: 16px; border-top: 1px solid #e5e5e5;">'
        f'<p style="margin: 0 0 4px; color: #666; font-size: 13px;">{escape(t.reference)} · '
        f'{escape(t.get_urgency_display())}</p>{_new_ticket_html(t, "h3", _NEW_TICKETS_EXCERPT)}</div>'
        for t in tickets
    )
    body = f"""
        <p style="margin: 0 0 18px;">{len(tickets)} new tickets since the last update
        {f'({urgent} urgent)' if urgent else ''}:</p>
        {blocks}
    """
    refs = ', '.join(t.reference for t in tickets[:4]) + (' …' if len(tickets) > 4 else '')
    subject = f'{len(tickets)} new tickets{f" ({urgent} urgent)" if urgent else ""}: {refs}'
    return _send(subject, wrap_html('New Client Tickets', body), [settings.EMAIL_RECIPIENT])


def send_ticket_confirmation(ticket):
    """Acknowledge the ticket to the client — only at a confirmed email address."""
    if not client_can_be_emailed(ticket):
        return False
    if ticket.urgency == ticket.Urgency.EMERGENCY:
        next_step = 'This is marked as an emergency, so the team will call you shortly.'
    else:
        next_step = 'The team will review it and you\'ll see updates on the ticket in your dashboard.'
    body = f"""
        <p style="margin: 0 0 12px;">Hi {escape(_client_name(ticket))},</p>
        <p style="margin: 0 0 16px;">We've received your ticket <strong>{escape(ticket.reference)}</strong>
        — <em>{escape(ticket.title)}</em>. {next_step}</p>
        {_detail_rows(ticket)}
        <p style="margin: 16px 0 0; color: #666; font-size: 13px;">
            Power out or a safety risk? Don't wait on the ticket — call us on +27 68 319 3323.
        </p>
    """
    return _send(
        f'We received your ticket {ticket.reference}',
        wrap_html('Ticket Received', body),
        [ticket.client.email],
    )


def ping_telegram_emergency(ticket):
    """Emergency tickets also go to the ops Telegram chat, if alerts are enabled."""
    if ticket.urgency != ticket.Urgency.EMERGENCY:
        return False
    if not getattr(settings, 'ALERT_TELEGRAM_ENABLED', False):
        return False
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', None)
    if not token or not chat_id:
        return False
    phone = _client_phone(ticket)
    text = (
        f'🚨 <b>EMERGENCY ticket {escape(ticket.reference)}</b>\n'
        f'{escape(_client_name(ticket))} — {escape(ticket.site.name)}\n'
        f'{escape(ticket.service)}: {escape(ticket.title)}\n'
        f'Account: {escape(ticket.client.email or ticket.client.get_username())}'
        f'{" (" + escape(phone) + ")" if phone else ""}\n'
        f'On site: {escape(" · ".join(filter(None, [ticket.site_contact_name, ticket.site_contact_phone])) or "—")}'
    )
    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f'Emergency Telegram ping for {ticket.reference} failed: {exc}')
        return False


# What each status means for the client, in the status-update email.
_STATUS_NOTES = {
    'open': 'Your ticket is open and waiting for the team.',
    'visit_booked': 'A visit has been booked.',
    'on_site': 'Our technician is on site.',
    'in_progress': 'The team is working on it.',
    'quote_to_approve': 'There is a quote waiting for your approval.',
    'info_needed': 'We need a bit more information from you.',
    'completed': 'The job is complete.',
    'cancelled': 'The ticket has been cancelled.',
}


def send_status_update(ticket, old_status=None):
    """Tell the client their ticket's status changed — confirmed emails only."""
    profile = _profile(ticket.client)
    if not (ticket.client.email and profile and profile.email_verified):
        return False
    status = ticket.get_status_display()
    note = _STATUS_NOTES.get(ticket.status, '')
    technician = (f'<p style="margin: 0 0 12px;">Technician: <strong>{escape(ticket.technician_name)}</strong></p>'
                  if ticket.technician_name else '')
    action = ''
    if ticket.status in (ticket.Status.QUOTE_TO_APPROVE, ticket.Status.INFO_NEEDED):
        action = ('<p style="margin: 0 0 12px;">The team will be in touch with the details, or you can reply '
                  'to this email.</p>')
    url = f'{settings.FRONTEND_BASE_URL}/dashboard/tickets?q={ticket.reference}'
    body = f"""
        <p style="margin: 0 0 12px;">Hi {escape(_client_name(ticket))},</p>
        <p style="margin: 0 0 12px;">Your ticket <strong>{escape(ticket.reference)}</strong>
        — <em>{escape(ticket.title)}</em> is now <strong>{escape(status)}</strong>.</p>
        <p style="margin: 0 0 12px;">{escape(note)}</p>
        {technician}
        {action}
        <p style="margin: 16px 0 0;"><a href="{escape(url)}" style="color: {ACCENT};">View your tickets</a></p>
        <p style="margin: 16px 0 0; color: #666; font-size: 13px;">
            Power out or a safety risk? Call us on +27 68 319 3323.
        </p>
    """
    return _send(f'Ticket {ticket.reference}: {status}', wrap_html('Ticket Update', body), [ticket.client.email])


# Longest chat excerpt shown per message, and messages shown per ticket, in
# an unread-messages email (the rest is in the dashboard).
_DIGEST_EXCERPT = 600
_DIGEST_PER_TICKET = 5


def _digest_ticket_html(ticket, messages, link_text):
    shown = messages[-_DIGEST_PER_TICKET:]
    hidden = len(messages) - len(shown)
    url = f'{settings.FRONTEND_BASE_URL}/dashboard/tickets/{ticket.pk}'
    items = ''.join(
        f'''<div style="margin: 0 0 10px; padding: 10px 12px; background: #f8f9fa; border-left: 3px solid {ACCENT};
               border-radius: 4px; font-size: 14px;">
            <div style="color: #666; font-size: 12px; margin-bottom: 4px;">
                {escape(_author_name(m))} · {timezone.localtime(m.created_at).strftime('%d/%m/%Y %H:%M')}
            </div>
            {linebreaks(escape(m.body[:_DIGEST_EXCERPT] + ('…' if len(m.body) > _DIGEST_EXCERPT else '')))}
        </div>'''
        for m in shown
    )
    more = (f'<p style="margin: 0 0 10px; color: #666; font-size: 13px;">…and {hidden} earlier '
            f'message{"s" if hidden != 1 else ""}.</p>') if hidden else ''
    return f"""
        <div style="margin: 0 0 22px;">
            <h3 style="margin: 0 0 8px; font-size: 15px;">
                {escape(ticket.reference)} — {escape(ticket.title)}
                <span style="color: #666; font-weight: normal;">({escape(ticket.site.name)})</span>
            </h3>
            {more}{items}
            <a href="{escape(url)}" style="color: {ACCENT}; font-weight: bold; font-size: 14px;">{escape(link_text)}</a>
        </div>
    """


def _author_name(message):
    if message.author_role == message.Author.CLIENT:
        return _client_name(message.ticket)
    first = (message.author.first_name or '').strip() if message.author else ''
    return f'{first} · APS' if first else 'APS team'


def send_client_chat_digest(client, tickets):
    """Unread APS replies across a client's tickets — one email. ``tickets`` maps
    ticket → its unread messages (oldest first)."""
    count = sum(len(m) for m in tickets.values())
    first_ticket = next(iter(tickets))
    body = f"""
        <p style="margin: 0 0 12px;">Hi {escape(_client_name(first_ticket))},</p>
        <p style="margin: 0 0 18px;">You have {count} unread message{'s' if count != 1 else ''} from APS
        about {'your ticket' if len(tickets) == 1 else f'{len(tickets)} of your tickets'}:</p>
        {''.join(_digest_ticket_html(t, msgs, 'Reply in your dashboard') for t, msgs in tickets.items())}
        <p style="margin: 8px 0 0; color: #666; font-size: 13px;">
            Power out or a safety risk? Call us on +27 68 319 3323.
        </p>
    """
    subject = (f'New message on ticket {first_ticket.reference}: {first_ticket.title}' if len(tickets) == 1
               else f'{count} new messages on your APS tickets')
    return _send(subject, wrap_html('New Messages', body), [client.email])


def send_team_chat_digest(tickets):
    """Unread client messages across all tickets — one email to the team."""
    count = sum(len(m) for m in tickets.values())
    blocks = ''.join(
        f'<p style="margin: 0 0 6px; font-weight: bold;">{escape(_client_name(t))}</p>'
        + _digest_ticket_html(t, msgs, 'Open the ticket in the dashboard')
        for t, msgs in tickets.items()
    )
    body = f"""
        <p style="margin: 0 0 18px;">{count} client message{'s' if count != 1 else ''} on
        {len(tickets)} ticket{'s' if len(tickets) != 1 else ''} {'has' if count == 1 else 'have'} not been read yet:</p>
        {blocks}
    """
    return _send(f'{count} unread client message{"s" if count != 1 else ""} on '
                 f'{len(tickets)} ticket{"s" if len(tickets) != 1 else ""}',
                 wrap_html('Unread Client Messages', body), [settings.EMAIL_RECIPIENT])
