"""Tool booking alerts: booked out, returned, problem flagged, and the daily overdue digest."""

import pytest

from apps.tools import emails
from apps.tools.models import Booking, Movement, ToolSettings

from ..client_portal.conftest import api_for
from .conftest import BOOKINGS, add_tool, book, days_ago, give_back, make_staff

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def locmem(settings):
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    row = ToolSettings.load()
    row.alert_emails = 'office@aps.example, accounts@aps.example'
    row.save()


def test_booked_and_returned_alerts_go_to_the_office_and_the_technician(
        api, lucky, mailoutbox, django_capture_on_commit_callbacks):
    drill = add_tool(api, serial='SN-1')
    with django_capture_on_commit_callbacks(execute=True):
        b = book(api, lucky, [drill['id']], notify=True, notes='For <script> Paarl')
    assert [m.to for m in mailoutbox] == [['office@aps.example', 'accounts@aps.example'], ['lucky@aps.example']]
    assert mailoutbox[0].subject == 'APS: Tools booked out to Lucky — TB-0001 (1 tool)'
    assert 'Hammer drill · Makita · SN-1' in mailoutbox[0].body
    assert '<script>' not in mailoutbox[0].body  # staff text is escaped
    assert '/dashboard/tools/booking/' in mailoutbox[0].body
    assert '/dashboard/tools/booking/' not in mailoutbox[1].body  # Lucky can't open the page
    assert Movement.objects.get(booking_id=b['id'], kind='out').alert_sent_at is not None

    with django_capture_on_commit_callbacks(execute=True):
        give_back(api, b, notify=True)
    assert mailoutbox[2].subject == 'APS: Tools returned by Lucky — TB-0001'
    assert 'Nothing — all back' in mailoutbox[2].body


def test_notify_off_sends_nothing(api, lucky, mailoutbox, django_capture_on_commit_callbacks):
    drill = add_tool(api)
    with django_capture_on_commit_callbacks(execute=True):
        b = book(api, lucky, [drill['id']], notify=False)
        give_back(api, b, notify=False)
    assert mailoutbox == []


def test_a_flagged_problem_always_alerts(api, lucky, mailoutbox, django_capture_on_commit_callbacks):
    booker = api_for(make_staff('ayesha', perms=['tools.view', 'tools.book']))
    checker = api_for(make_staff('sediek', perms=['tools.view', 'tools.check'], first_name='Sediek'))
    b = book(booker, lucky, [add_tool(api)['id']], notify=False)
    with django_capture_on_commit_callbacks(execute=True):
        checker.post(f'{BOOKINGS}check/', {'bookings': [b['id']], 'problem': True, 'note': 'Charger missing'},
                     format='json')
    assert [m.subject for m in mailoutbox] == ['APS: Problem flagged on TB-0001 (Lucky)'] * 2
    assert 'Charger missing' in mailoutbox[0].body and 'Sediek' in mailoutbox[0].body


def test_overdue_digest(api, lucky, mailoutbox):
    leo = make_staff('leonard', perms=[], first_name='Leonard')  # no email
    old = book(api, lucky, [add_tool(api, name='Grinder')['id']], date_out=days_ago(40))
    book(api, leo, [add_tool(api, name='Saw')['id']], date_out=days_ago(5), due_date=days_ago(2))
    book(api, lucky, [add_tool(api, name='Drill')['id']], date_out=days_ago(90), long_term_kit=True)  # kits never
    book(api, lucky, [add_tool(api, name='Ladder')['id']], date_out=days_ago(2))  # not yet

    assert emails.send_overdue_digest() == '2 overdue booking(s), 2 email(s) sent'
    office, tech = mailoutbox
    assert office.subject == 'APS: 2 tool bookings overdue' and office.to == ['office@aps.example',
                                                                              'accounts@aps.example']
    assert 'Grinder' in office.body and 'Saw' in office.body and 'Drill' not in office.body
    assert 'out 40 days, no due date' in office.body and '2 days late' in office.body
    assert tech.to == ['lucky@aps.example'] and 'Grinder' in tech.body and 'Saw' not in tech.body
    assert Booking.objects.get(pk=old['id']).overdue_alert_at is not None


def test_the_digest_can_be_switched_off_without_stopping_the_other_alerts(
        api, lucky, mailoutbox, django_capture_on_commit_callbacks):
    api.patch('/tools/settings/', {'overdue_emails': False}, format='json')
    book(api, lucky, [add_tool(api, name='Grinder')['id']], date_out=days_ago(40))
    assert emails.send_overdue_digest() == 'paused (Overdue emails is switched off)'
    assert mailoutbox == []
    with django_capture_on_commit_callbacks(execute=True):
        book(api, lucky, [add_tool(api)['id']], notify=True)
    assert len(mailoutbox) == 2


def test_nothing_overdue_sends_nothing(api, lucky, mailoutbox):
    book(api, lucky, [add_tool(api)['id']])
    assert emails.send_overdue_digest() == 'nothing overdue'
    assert mailoutbox == []

