"""
Tool booking alerts (the sheet's booking / returned / overdue emails).

- **Booked out** and **returned**: when the booking's / return's "Send the
  alert" switch is on.
- **Problem flagged** by a checker: always.
- **Overdue digest**: once a day at 09:00 (``tasks.send_overdue_digest``), one
  email to the office listing every overdue booking, plus one to each
  technician with an email listing theirs. Nothing when nothing is overdue, or
  when "Overdue emails" is switched off in Tool settings.

Every alert goes to the office's list (``ToolSettings.alert_emails``) and
the technician, when their account has an email. They're queued after the
save by Celery (sent straight away if the queue is down) through ``notify``:
the one place a later in-app notification can hook into. Everything typed by
staff is HTML-escaped. Failures are logged, never raised. Imports and
management commands never send anything.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from apps.core.mail import ACCENT, button_html, send_html, wrap_html
from apps.staff.access import has_perm

from .models import Booking, BookingItem, Movement, ToolSettings, person_name

logger = logging.getLogger(__name__)


def notify(kind, *args):
    """Queue alert ``kind`` ('booked', 'returned', 'flagged') once the current transaction commits."""
    def go():
        from . import tasks
        task = {'booked': tasks.send_booked_alert, 'returned': tasks.send_returned_alert,
                'flagged': tasks.send_flagged_alert}[kind]
        try:
            task.delay(*args)
        except Exception:  # noqa: BLE001 — queue down: send now rather than lose it
            logger.warning('Celery unavailable; sending the tool %s alert straight away.', kind, exc_info=True)
            task(*args)

    transaction.on_commit(go)


def notify_booked(booking):
    notify('booked', booking.pk)


def notify_returned(movement):
    notify('returned', movement.pk)


def notify_flagged(booking, movement_ids):
    notify('flagged', booking.pk, list(movement_ids))


# ── building blocks ──────────────────────────────────────────────────────────

def _row(label, value):
    return (f'<tr><td style="padding: 4px 12px 4px 0; color: #666; white-space: nowrap; vertical-align: top;">{label}</td>'
            f'<td style="padding: 4px 0;">{value}</td></tr>')


def _date(value):
    return value.strftime('%a %d %b %Y') if value else '—'


def _items_html(items):
    lines = []
    for item in items:
        qty = f' ×{item.quantity}' if item.quantity > 1 else ''
        extra = ''
        if item.return_condition and item.return_condition != BookingItem.Condition.OK:
            extra = (f' <strong style="color: #b42318;">{escape(item.get_return_condition_display())}'
                     f'{": " + escape(item.return_note) if item.return_note else ""}</strong>')
        lines.append(f'<li style="margin: 2px 0;">{escape(item.tool_label)}{qty}{extra}</li>')
    return f'<ul style="margin: 8px 0 12px; padding-left: 20px;">{"".join(lines)}</ul>'


def _link(booking, user=None):
    """'Open the booking' for the office list, and for a technician who can open the page."""
    if user is not None and not has_perm(user, 'tools.view'):
        return ''
    return button_html(f'{settings.FRONTEND_BASE_URL}/dashboard/tools/bookings/{booking.pk}', 'Open the booking')


def _recipients(booking):
    """(office addresses, the technician if they have an email and aren't on the list already)."""
    office = ToolSettings.load().alert_list()
    tech = booking.technician
    tech_email = tech.email if tech.is_active and tech.email and tech.email.lower() not in {
        e.lower() for e in office} else ''
    return office, tech_email


def _send(subject, heading, booking, body, log_prefix):
    """The office gets one email; the technician their own copy (their link only if they can open it)."""
    office, tech_email = _recipients(booking)
    sent = False
    if office:
        sent |= send_html(subject, wrap_html(heading, body + _link(booking)), office, log_prefix=log_prefix)
    if tech_email:
        sent |= send_html(subject, wrap_html(heading, body + _link(booking, booking.technician)), [tech_email],
                          log_prefix=log_prefix)
    if not office and not tech_email:
        logger.warning('%s for %s skipped: no alert emails set and the technician has no email.',
                       log_prefix, booking.booking_id)
    return sent


def _booking(pk):
    return (Booking.all_objects.select_related('technician', 'job')
            .prefetch_related('items', 'movements').filter(pk=pk).first())


# ── the alerts ───────────────────────────────────────────────────────────────

def send_booked(booking_id):
    booking = _booking(booking_id)
    if booking is None or booking.in_trash:  # undone before the task ran
        return False
    tech = person_name(booking.technician)
    rows = [
        _row('Booking', f'<strong>{escape(booking.booking_id)}</strong>'),
        _row('Booked out to', escape(tech)),
        _row('Date out', _date(booking.date_out)),
        _row('Due back', 'Long-term kit (no due date)' if booking.long_term_kit else _date(booking.due_date)),
        _row('Job', escape(booking.job.job_id)) if booking.job_id else '',
        _row('Notes', escape(booking.notes)) if booking.notes else '',
    ]
    body = f"""
        <p style="margin: 0 0 12px;">Tools have been booked out:</p>
        <table style="border-collapse: collapse; font-size: 14px;">{''.join(rows)}</table>
        {_items_html(booking.items.all())}
    """
    n = sum(i.quantity for i in booking.items.all())
    subject = f'APS: Tools booked out to {tech} — {booking.booking_id} ({n} tool{"s" if n != 1 else ""})'
    sent = _send(subject, 'Tools booked out', booking, body, 'Tool booked-out alert')
    if sent:
        Movement.objects.filter(booking=booking, kind=Movement.Kind.OUT).update(alert_sent_at=timezone.now())
    return sent


def send_returned(movement_id):
    movement = Movement.objects.select_related('booking__technician').filter(pk=movement_id).first()
    if movement is None:
        return False
    booking = _booking(movement.booking_id)
    tech = person_name(booking.technician)
    still_out = [i for i in booking.items.all() if i.is_out]
    rows = [
        _row('Booking', f'<strong>{escape(booking.booking_id)}</strong>'),
        _row('Booked out to', escape(tech)),
        _row('Date out', _date(booking.date_out)),
        _row('Returned', _date(movement.date)),
        _row('Still out', escape(', '.join(i.tool_label for i in still_out)) if still_out else 'Nothing — all back'),
    ]
    body = f"""
        <p style="margin: 0 0 12px;">Tools have been returned:</p>
        <table style="border-collapse: collapse; font-size: 14px;">{''.join(rows)}</table>
        {_items_html(movement.items.all())}
        <p style="margin: 0 0 12px; color: #666;">{'Checked on return.' if movement.is_checked
                                                    else 'Waiting for a checker to confirm what came back.'}</p>
    """
    subject = f'APS: Tools returned by {tech} — {booking.booking_id}'
    sent = _send(subject, 'Tools returned', booking, body, 'Tool returned alert')
    if sent:
        Movement.objects.filter(pk=movement.pk).update(alert_sent_at=timezone.now())
    return sent


def send_flagged(booking_id, movement_ids):
    booking = _booking(booking_id)
    if booking is None:
        return False
    movements = [m for m in booking.movements.all() if m.pk in set(movement_ids)]
    if not movements:
        return False
    first = movements[0]
    checker = person_name(first.checked_by) if first.checked_by_id else 'A checker'
    what = ' and '.join(dict.fromkeys(m.get_kind_display().lower() for m in movements))
    items = [i for i in booking.items.all()
             if any(m.kind == Movement.Kind.OUT for m in movements) or i.return_movement_id in set(movement_ids)]
    body = f"""
        <p style="margin: 0 0 12px;"><strong>{escape(checker)}</strong> flagged a problem while checking the
           {escape(what)} of <strong>{escape(booking.booking_id)}</strong> ({escape(person_name(booking.technician))}):</p>
        <div style="margin: 12px 0; padding: 10px 12px; background: #fdf3f2; border-left: 3px solid {ACCENT};">
          {escape(first.check_note)}</div>
        {_items_html(items)}
    """
    subject = f'APS: Problem flagged on {booking.booking_id} ({person_name(booking.technician)})'
    return _send(subject, 'Tool problem flagged', booking, body, 'Tool problem alert')


# ── the overdue digest ───────────────────────────────────────────────────────

def overdue_bookings(settings_row=None, today=None):
    from .services import booking_state, overdue_q, with_flags
    settings_row = settings_row or ToolSettings.load()
    today = today or timezone.localdate()
    qs = (with_flags(Booking.objects.all()).filter(overdue_q(settings_row, today))
          .select_related('technician').prefetch_related('items', 'movements').order_by('date_out', 'number'))
    return [(b, booking_state(b, settings_row, today)) for b in qs]


def _digest_table(rows, today):
    lines = []
    for booking, state in rows:
        tools = ', '.join(f'{i.tool_label}{" ×" + str(i.quantity) if i.quantity > 1 else ""}' for i in state['open'])
        why = (f'due {booking.due_date:%d %b} ({(today - booking.due_date).days} days late)' if booking.due_date
               else f'out {state["days_out"]} days, no due date')
        link = f'{settings.FRONTEND_BASE_URL}/dashboard/tools/bookings/{booking.pk}'
        lines.append(
            '<tr>'
            f'<td style="padding: 6px 10px 6px 0; vertical-align: top; white-space: nowrap;">'
            f'<a href="{escape(link)}" style="color: {ACCENT};">{escape(booking.booking_id)}</a></td>'
            f'<td style="padding: 6px 10px 6px 0; vertical-align: top;">{escape(person_name(booking.technician))}</td>'
            f'<td style="padding: 6px 10px 6px 0; vertical-align: top;">{escape(tools)}</td>'
            f'<td style="padding: 6px 0; vertical-align: top; white-space: nowrap; color: #b42318;">{escape(why)}</td>'
            '</tr>')
    return f'<table style="border-collapse: collapse; font-size: 14px;">{"".join(lines)}</table>'


def send_overdue_digest(today=None):
    """The daily digest. Returns a short summary for the task result."""
    settings_row = ToolSettings.load()
    if not settings_row.overdue_emails:
        return 'paused (Overdue emails is switched off)'
    today = today or timezone.localdate()
    rows = overdue_bookings(settings_row, today)
    if not rows:
        return 'nothing overdue'
    n = len(rows)
    office = settings_row.alert_list()
    sent = 0
    if office:
        body = f"""
            <p style="margin: 0 0 12px;">{n} tool booking{'s are' if n != 1 else ' is'} overdue and not returned:</p>
            {_digest_table(rows, today)}
            {button_html(f'{settings.FRONTEND_BASE_URL}/dashboard/tools/bookings?tile=overdue', 'See overdue bookings')}
        """
        if send_html(f'APS: {n} tool booking{"s" if n != 1 else ""} overdue', wrap_html('Overdue tools', body),
                     office, log_prefix='Tool overdue digest'):
            sent += 1
    by_tech = {}
    for booking, state in rows:
        by_tech.setdefault(booking.technician_id, []).append((booking, state))
    office_lower = {e.lower() for e in office}
    for tech_rows in by_tech.values():
        tech = tech_rows[0][0].technician
        if not (tech.is_active and tech.email) or tech.email.lower() in office_lower:
            continue
        body = f"""
            <p style="margin: 0 0 12px;">Hi {escape(tech.first_name or person_name(tech))},</p>
            <p style="margin: 0 0 12px;">These tools are overdue. Please bring them back to the office or
               ask for a new due date.</p>
            {_digest_table(tech_rows, today)}
        """
        if send_html('APS: Your overdue tools', wrap_html('Overdue tools', body), [tech.email],
                     log_prefix='Tool overdue digest'):
            sent += 1
    Booking.objects.filter(pk__in=[b.pk for b, _ in rows]).update(overdue_alert_at=timezone.now())
    return f'{n} overdue booking(s), {sent} email(s) sent'
