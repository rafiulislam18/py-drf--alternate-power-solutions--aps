"""
Every action in the dashboard (not reads) lands in the audit history — staff
& admin on one page, clients on the other (``?kind=client``).
"""

from datetime import datetime, timezone as dt_timezone

import pytest
from django.contrib.auth.models import User

from apps.core.models import ClientProfile
from apps.records.models import AuditLog
from apps.solar_dashboard.models import Site
from apps.whatsapp_import.models import WhatsAppMessage

from ..client_portal.conftest import PASSWORD, api_for, login, make_client, make_service, make_ticket
from ..solar_estimates.conftest import catalogue, inputs  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db
AUDIT = '/records/audit/'


@pytest.fixture
def boss(db):
    user = User.objects.create_user('boss', password=PASSWORD, is_staff=True, is_superuser=True)
    ClientProfile.objects.create(user=user, role='admin')
    return user


@pytest.fixture
def acme(db):
    client = make_client('acme', email='sipho@acme.example', company_name='Acme')
    return client, Site.objects.create(client=client, name='Head office')


def actions(**filters):
    return list(AuditLog.objects.filter(**filters).order_by('id').values_list('action', flat=True))


def test_staff_and_client_pages_are_separate(boss, acme):
    client, site = acme
    service = make_service('Electrical')
    assert login('acme').status_code == 200 and login('boss').status_code == 200

    res = api_for(client).post('/client-portal/tickets/', {
        'title': 'Inverter beeping', 'description': 'Since Monday', 'service': service.pk,
        'site': site.pk, 'urgency': 'normal'}, format='json')
    assert res.status_code == 201, res.data
    ticket = res.data['id']
    api_for(client).post(f'/client-portal/tickets/{ticket}/messages/', {'body': 'Any news?'}, format='json')
    api_for(boss).patch(f'/client-portal/staff/tickets/{ticket}/', {'status': 'in_progress'}, format='json')

    staff_page = api_for(boss).get(AUDIT).data
    client_page = api_for(boss).get(AUDIT, {'kind': 'client'}).data
    assert [e['action'] for e in staff_page['results']] == ['updated', 'signed_in']
    assert [e['action'] for e in client_page['results']] == ['message_sent', 'created', 'signed_in']
    assert {e['actor_role'] for e in client_page['results']} == {'client'}
    assert staff_page['kind'] == 'staff' and client_page['kind'] == 'client'
    assert client_page['filters']['actors'] == [{'id': client.pk, 'name': client.get_full_name() or 'acme'}]
    status_change = staff_page['results'][0]
    assert status_change['type_label'] == 'Ticket' and status_change['changes'][0]['field'] == 'status'

    # A ticket's own history shows both sides.
    history = api_for(boss).get(f'/records/history/client_portal.ticket/{ticket}/').data
    assert [e['actor_role'] for e in history] == ['staff', 'client', 'client']


def test_account_actions(acme):
    client, _ = acme
    api = api_for(client)
    api.patch('/accounts/me/', {'phone': '082 000 1111'}, format='json')
    api.post('/accounts/password/change/', {'current_password': PASSWORD, 'password': 'Brand-New-Pass-77',
                                            'confirm_password': 'Brand-New-Pass-77'}, format='json')
    assert actions(actor=client) == ['updated', 'password_changed']
    edit = AuditLog.objects.filter(action='updated').get()
    assert edit.changes['phone']['new'] == '082 000 1111' and edit.actor_role == 'client'


def test_staff_accounts_are_audited(boss):
    api = api_for(boss)
    res = api.post('/staff/accounts/', {'username': 'thandi', 'name': 'Thandi M', 'password': 'Sunny-Days-2026',
                                        'confirm_password': 'Sunny-Days-2026', 'permissions': ['reports.view']},
                   format='json')
    api.patch(f"/staff/accounts/{res.data['id']}/", {'permissions': ['reports.view', 'reports.edit'],
                                                     'is_active': False}, format='json')
    created, updated = AuditLog.objects.filter(section='staff').order_by('id')
    assert created.action == 'created' and created.object_repr == 'Thandi M (thandi)'
    assert updated.changes['permissions']['new'] == 'Added: Edit reports'
    assert updated.changes['is_active'] == {'label': 'Active', 'old': 'Yes', 'new': 'No'}


def test_clients_and_sites_are_audited(boss, acme):
    client, site = acme
    api = api_for(boss)
    res = api.post('/dashboard/clients/create/', {'username': 'bayside', 'password': 'Sunny-Days-2026',
                                                  'confirm_password': 'Sunny-Days-2026', 'company_name': 'Bayside'},
                   format='json')
    assert res.status_code == 201, res.data
    api.patch(f'/dashboard/sites/{site.pk}/', {'name': 'HQ'}, format='json')
    assert actions(section='clients') == ['created', 'updated']
    assert AuditLog.objects.get(action='updated').changes['name'] == {'label': 'Name', 'old': 'Head office',
                                                                     'new': 'HQ'}


def test_estimates_are_audited(boss, catalogue):  # noqa: F811
    api = api_for(boss)
    pk = api.post('/solar-estimates/', {'inputs': inputs()}, format='json').data['id']
    api.patch(f'/solar-estimates/{pk}/', {'inputs': inputs(client_name='Seaview Trust')}, format='json')
    api.post(f'/solar-estimates/{pk}/confirm/', {}, format='json')
    api.post(f'/solar-estimates/{pk}/reopen/')
    api.delete(f'/solar-estimates/{pk}/')
    assert actions(section='estimates') == ['created', 'updated', 'confirmed', 'reopened', 'deleted']
    edit = AuditLog.objects.get(action='updated')
    assert edit.changes['client_name'] == {'label': 'Client name', 'old': 'Test Trust', 'new': 'Seaview Trust'}
    assert 'inputs' not in edit.changes  # JSON blobs stay out


def test_whatsapp_actions_are_audited(boss):
    msg = WhatsAppMessage.objects.create(sender='Sediek', text='Geyser leaking at no. 4', chat_name='House Brown',
                                         sent_at=datetime(2026, 7, 10, 8, 0, tzinfo=dt_timezone.utc))
    api = api_for(boss)
    api.patch(f'/whatsapp/messages/{msg.pk}/mark/', {'marked_as_job': True}, format='json')
    api.patch(f'/whatsapp/messages/{msg.pk}/mark/', {'marked_as_job': True}, format='json')  # no change
    api.post('/whatsapp/messages/bulk-dismiss/', {'ids': [msg.pk], 'dismissed': True}, format='json')
    assert actions(section='whatsapp') == ['marked_job', 'dismissed']


def test_audit_pages_need_the_permission(boss, acme):
    from apps.staff.models import StaffAccess
    reader = User.objects.create_user('reader', password='x')
    ClientProfile.objects.create(user=reader, role='admin')
    StaffAccess.objects.create(user=reader, permissions=['tickets.view'])
    for kind in ('staff', 'client'):
        assert api_for(reader).get(AUDIT, {'kind': kind}).status_code == 403
        assert api_for(acme[0]).get(AUDIT, {'kind': kind}).status_code == 403
    # …but the history of a ticket they can see is fine.
    ticket = make_ticket(acme[0], acme[1])
    assert api_for(reader).get(f'/records/history/client_portal.ticket/{ticket.pk}/').status_code == 200
    assert api_for(reader).get(f'/records/history/solar_estimates.solarestimate/1/').status_code == 403
