"""
Push for overdue tool bookings (APS dashboard staff).

An hourly check, 07:00–18:00 SAST (migration push 0003), so a booking that went
overdue overnight is announced in the morning, never at midnight. Overdue = the
same rule as the Overdue tile (``apps.tools.services.overdue_q``; long-term kits
never). Once per booking: ``Booking.overdue_push_at`` is stamped when it's sent
and cleared once the booking stops being overdue (returned, due date moved), so
going overdue again notifies again. Not repeated daily.

Goes to the technician, staff with ``tools.check`` and accounts with "Receive
admin alerts", as long as they can see Tool bookings (``tools.view``) and haven't
switched overdue tools off. "{tools} overdue · {technician}".
"""

import logging

from django.utils import timezone

from apps.tools.models import Booking, ToolSettings, person_name
from apps.tools.services import overdue_q, with_flags

from .prefs import TOOL_OVERDUE, admin_alert_users, staff_with_perm, subscriptions_for
from .send import deliver

logger = logging.getLogger('apps.push')

TOOLS_LISTED = 2


def tools_text(booking):
    names = [item.tool.name if item.tool_id and item.tool else item.tool_label
             for item in booking.items.all() if item.is_out]
    text = ', '.join(names[:TOOLS_LISTED]) or 'Tools'
    return text + (f' +{len(names) - TOOLS_LISTED} more' if len(names) > TOOLS_LISTED else '')


def overdue_payload(booking):
    when = (f'due {booking.due_date:%a %d %b}' if booking.due_date
            else f'out since {booking.date_out:%a %d %b}')
    return {
        'app': 'aps',
        'title': f'{tools_text(booking)} overdue · {person_name(booking.technician)}',
        'body': f'{booking.booking_id} · {when}',
        'tag': f'aps-booking-{booking.pk}',
        'url': f'/dashboard/tools/bookings/{booking.pk}',
    }


def check_overdue_tools(now=None):
    """Push for bookings that became overdue since the last run. Returns
    {'new': n, 'cleared': n, 'sent': n}."""
    now = now or timezone.now()
    overdue = with_flags(Booking.objects.all()).filter(overdue_q(ToolSettings.load(), timezone.localdate(now)))
    overdue_ids = set(overdue.values_list('pk', flat=True))
    # No longer overdue (returned, due date moved later, made a kit): may notify again later.
    cleared = (Booking.all_objects.filter(overdue_push_at__isnull=False).exclude(pk__in=overdue_ids)
               .update(overdue_push_at=None))
    new = list(Booking.objects.filter(pk__in=overdue_ids, overdue_push_at__isnull=True)
               .select_related('technician').prefetch_related('items__tool').order_by('number'))
    result = {'new': 0, 'cleared': cleared, 'sent': 0}
    if not new:
        return result
    team = {u.pk: u for u in staff_with_perm('tools.check') + admin_alert_users(TOOL_OVERDUE)}
    for booking in new:
        # Stamp first: an overlapping run can't send it twice.
        if not Booking.objects.filter(pk=booking.pk, overdue_push_at__isnull=True).update(overdue_push_at=now):
            continue
        result['new'] += 1
        people = {**team, booking.technician_id: booking.technician}
        subs = subscriptions_for(people.values(), TOOL_OVERDUE)
        if subs:
            result['sent'] += deliver(subs, overdue_payload(booking))['sent']
    logger.info(f'Overdue tools push: {result}')
    return result
