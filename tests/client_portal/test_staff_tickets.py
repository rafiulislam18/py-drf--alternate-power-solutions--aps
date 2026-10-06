"""
Tests for the staff side of client tickets (/client-portal/staff/...): who may
use it, the all-clients list (filters, client filter, search by client,
sorting, pagination), the summary, the ticket page with attachments, and
status/technician updates with the optional client email.
"""

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.client_portal.models import Ticket, TicketAttachment
from apps.core.models import ClientProfile

from .conftest import api_for, make_client, make_ticket

LIST_URL = '/client-portal/staff/tickets/'
SUMMARY_URL = '/client-portal/staff/tickets/summary/'


def detail_url(ticket):
    return f'/client-portal/staff/tickets/{ticket.pk}/'


@pytest.fixture
def staff(admin_user):
    return api_for(admin_user)


@pytest.fixture
def tickets(client_user, site, other_client, other_site):
    return [
        make_ticket(client_user, site, title='Geyser leak', service='plumbing'),
        make_ticket(client_user, site, title='DB board trips', status=Ticket.Status.VISIT_BOOKED,
                    urgency=Ticket.Urgency.EMERGENCY),
        make_ticket(other_client, other_site, title='Roof paint', service='painting',
                    status=Ticket.Status.COMPLETED),
    ]


# ── who may use it ──────────────────────────────────────────────────────────


@pytest.mark.parametrize('url', [LIST_URL, SUMMARY_URL])
def test_requires_sign_in(db, url):
    assert api_for().get(url).status_code == 401


@pytest.mark.parametrize('url', [LIST_URL, SUMMARY_URL])
def test_clients_are_refused(api, url):
    assert api.get(url).status_code == 403


def test_client_cannot_open_or_update_a_ticket(api, tickets):
    t = tickets[0]
    assert api.get(detail_url(t)).status_code == 403
    assert api.patch(detail_url(t), {'status': 'completed'}, format='json').status_code == 403
    t.refresh_from_db()
    assert t.status == Ticket.Status.OPEN


def test_staff_without_profile_and_admin_role_profile_allowed(db, staff_user):
    assert api_for(staff_user).get(LIST_URL).status_code == 200
    office = make_client('aps-office')
    ClientProfile.objects.filter(user=office).update(role='admin')
    office.refresh_from_db()
    assert api_for(office).get(LIST_URL).status_code == 200


# ── list ────────────────────────────────────────────────────────────────────


def test_lists_every_clients_tickets_with_client(staff, tickets, client_user):
    res = staff.get(LIST_URL)
    assert res.status_code == 200
    assert res.data['count'] == 3
    row = next(r for r in res.data['results'] if r['title'] == 'Geyser leak')
    assert row['id'] == tickets[0].pk
    assert row['client'] == {
        'id': client_user.pk, 'name': 'Acme Properties', 'username': 'acme',
        'email': 'sipho@acme.example', 'email_verified': True, 'phone': '021 555 0100',
    }


def test_filter_by_client_status_group_and_urgency(staff, tickets, other_client):
    assert [r['title'] for r in staff.get(LIST_URL, {'client': other_client.pk}).data['results']] == ['Roof paint']
    assert [r['title'] for r in staff.get(LIST_URL, {'status_group': 'in_progress'}).data['results']] == ['DB board trips']
    assert [r['title'] for r in staff.get(LIST_URL, {'urgency': 'emergency'}).data['results']] == ['DB board trips']
    assert [r['title'] for r in staff.get(LIST_URL, {'status': 'completed'}).data['results']] == ['Roof paint']


@pytest.mark.parametrize('term', ['Other Co', 'otherco', 'ops@other', 'roof'])
def test_search_matches_client_and_ticket(staff, tickets, term):
    assert [r['title'] for r in staff.get(LIST_URL, {'search': term}).data['results']] == ['Roof paint']


def test_search_by_reference(staff, tickets):
    ref = tickets[1].reference
    assert [r['reference'] for r in staff.get(LIST_URL, {'search': ref}).data['results']] == [ref]


def test_sort_by_client(staff, tickets):
    names = [r['client']['name'] for r in staff.get(LIST_URL, {'ordering': 'client'}).data['results']]
    assert names == sorted(names)
    names_desc = [r['client']['name'] for r in staff.get(LIST_URL, {'ordering': '-client'}).data['results']]
    assert names_desc == sorted(names, reverse=True)


def test_pagination(staff, client_user, site):
    for i in range(12):
        make_ticket(client_user, site, title=f'Job {i}')
    first = staff.get(LIST_URL, {'page_size': 5}).data
    assert first['total_pages'] == 3 and len(first['results']) == 5


def test_odd_params_do_not_break(staff, tickets):
    res = staff.get(LIST_URL, {'client': 'abc', 'site': '²', 'ordering': 'nope', 'status': 'bogus'})
    assert res.status_code == 200 and res.data['count'] == 3


def test_summary_counts_all_or_one_client(staff, tickets, client_user):
    assert staff.get(SUMMARY_URL).data == {'open': 1, 'in_progress': 1, 'waiting': 0, 'completed': 1, 'all': 3}
    assert staff.get(SUMMARY_URL, {'client': client_user.pk}).data['all'] == 2


# ── ticket page ─────────────────────────────────────────────────────────────


def test_detail_includes_attachments(staff, tickets):
    t = tickets[0]
    TicketAttachment.objects.create(
        ticket=t, file=SimpleUploadedFile('p.png', b'\x89PNG\r\n\x1a\n'), original_name='leak.png',
        content_type='image/png', size=8,
    )
    res = staff.get(detail_url(t))
    assert res.status_code == 200
    assert res.data['attachment_count'] == 1
    att = res.data['attachments'][0]
    assert att['name'] == 'leak.png' and att['url'].startswith('http')
    assert res.data['description'] == t.description


def test_missing_ticket_404(staff, db):
    assert staff.get('/client-portal/staff/tickets/999999/').status_code == 404


# ── updates ─────────────────────────────────────────────────────────────────


def test_status_change_emails_verified_client(staff, tickets):
    t = tickets[0]
    res = staff.patch(detail_url(t), {'status': 'visit_booked', 'technician_name': '  Thabo   M ',
                                      'notify_client': True}, format='json')
    assert res.status_code == 200, res.data
    assert res.data['status'] == 'visit_booked' and res.data['technician_name'] == 'Thabo M'
    assert res.data['client_emailed'] is True
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ['sipho@acme.example']
    assert t.reference in msg.subject and 'Visit booked' in msg.subject
    assert 'Thabo M' in msg.body


def test_no_email_when_notify_off(staff, tickets):
    res = staff.patch(detail_url(tickets[0]), {'status': 'in_progress', 'notify_client': False}, format='json')
    assert res.status_code == 200 and res.data['client_emailed'] is False
    assert mail.outbox == []


def test_no_email_to_unconfirmed_address(staff, db):
    unverified = make_client('newco', email='x@new.example', company_name='NewCo', verified=False)
    from apps.solar_dashboard.models import Site
    t = make_ticket(unverified, Site.objects.create(client=unverified, name='A'))
    res = staff.patch(detail_url(t), {'status': 'completed'}, format='json')
    assert res.status_code == 200 and res.data['client_emailed'] is False
    assert mail.outbox == []


def test_technician_only_change_sends_nothing(staff, tickets):
    res = staff.patch(detail_url(tickets[0]), {'technician_name': 'Lerato'}, format='json')
    assert res.status_code == 200 and res.data['client_emailed'] is False
    assert mail.outbox == []


def test_bad_status_rejected_with_field_error(staff, tickets):
    res = staff.patch(detail_url(tickets[0]), {'status': 'teleported'}, format='json')
    assert res.status_code == 400
    assert 'status' in res.data


def test_staff_cannot_change_other_fields(staff, tickets, other_client):
    t = tickets[0]
    staff.patch(detail_url(t), {'title': 'Hacked', 'client': other_client.pk, 'description': 'x'}, format='json')
    t.refresh_from_db()
    assert t.title == 'Geyser leak' and t.client_id != other_client.pk


def test_email_escapes_client_text(staff, client_user, site):
    t = make_ticket(client_user, site, title='<script>alert(1)</script>')
    staff.patch(detail_url(t), {'status': 'completed'}, format='json')
    assert '<script>' not in mail.outbox[0].body
