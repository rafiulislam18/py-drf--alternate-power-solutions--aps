"""
Staff accounts & permissions (apps.staff): superusers create staff accounts and
tick their permissions per section; read permissions start on, create / edit /
delete (and the ticket extras) start off; every staff endpoint checks them.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from apps.client_portal.models import Ticket, TicketMessage
from apps.core.models import ClientProfile
from apps.staff import catalogue
from apps.staff.models import StaffAccess

from ..client_portal.conftest import PASSWORD, api_for, login, make_client, make_service, make_ticket

pytestmark = pytest.mark.django_db
ACCOUNTS = '/staff/accounts/'


@pytest.fixture
def boss(db):
    """A superuser — full access, manages staff."""
    user = User.objects.create_user('boss', password=PASSWORD, is_staff=True, is_superuser=True)
    ClientProfile.objects.create(user=user, role='admin')
    return user


@pytest.fixture
def boss_api(boss):
    return api_for(boss)


def make_staff(username='thandi', perms=None):
    user = User.objects.create_user(username, email=f'{username}@aps.example', password=PASSWORD)
    ClientProfile.objects.create(user=user, role='admin', verified_email=user.email)
    if perms is not None:
        StaffAccess.objects.create(user=user, permissions=perms)
    return user


@pytest.fixture
def client_and_site(db):
    from apps.solar_dashboard.models import Site
    client = make_client('acme', email='sipho@acme.example', company_name='Acme')
    return client, Site.objects.create(client=client, name='Head office')


# ── managing staff accounts ──────────────────────────────────────────────────

def test_only_superusers_manage_staff(boss_api, client_and_site):
    full = make_staff('full', perms=catalogue.ALL_PERMS)
    for api in (api_for(full), api_for(client_and_site[0]), api_for()):
        assert api.get(ACCOUNTS).status_code in (401, 403)
        assert api.get('/staff/permissions/').status_code in (401, 403)
    res = boss_api.get('/staff/permissions/')
    assert res.status_code == 200
    keys = [s['key'] for s in res.data['sections']]
    assert keys == ['reports', 'clients', 'tickets', 'quotes', 'estimates', 'whatsapp', 'services', 'projects', 'blogs',
                    'trash', 'audit']
    tickets = next(s for s in res.data['sections'] if s['key'] == 'tickets')
    assert [p['action'] for p in tickets['perms']] == ['view', 'create', 'edit', 'assign', 'chat_view', 'chat_send']
    assert set(res.data['default']) == {c for c in catalogue.ALL_PERMS if c.endswith(('.view', '.chat_view'))}


def test_create_staff_starts_with_read_permissions_and_can_sign_in(boss_api):
    res = boss_api.post(ACCOUNTS, {'username': 'thandi', 'name': 'Thandi Mokoena', 'email': 'thandi@aps.example',
                                   'password': 'Strong-Pass-2026', 'confirm_password': 'Strong-Pass-2026'}, format='json')
    assert res.status_code == 201, res.data
    assert res.data['permissions'] == catalogue.DEFAULT_PERMS and not res.data['is_superuser']
    user = User.objects.get(username='thandi')
    assert user.client_profile.role == 'admin' and not user.is_staff and user.get_full_name() == 'Thandi Mokoena'
    # signs in with the email too; the token reply carries the permissions
    token = login('thandi@aps.example', 'Strong-Pass-2026')
    assert token.status_code == 200, token.data
    assert token.data['role'] == 'admin' and token.data['is_superuser'] is False
    assert 'tickets.view' in token.data['permissions'] and 'tickets.edit' not in token.data['permissions']


def test_create_staff_validation(boss_api, client_and_site):
    base = {'username': 'x1', 'name': 'X', 'password': 'Strong-Pass-2026', 'confirm_password': 'Strong-Pass-2026'}
    assert boss_api.post(ACCOUNTS, {**base, 'username': 'ACME'}, format='json').status_code == 400   # taken (any case)
    assert boss_api.post(ACCOUNTS, {**base, 'email': 'sipho@acme.example'}, format='json').status_code == 400
    res = boss_api.post(ACCOUNTS, {**base, 'confirm_password': 'nope'}, format='json')
    assert res.status_code == 400 and 'confirm_password' in res.data
    assert boss_api.post(ACCOUNTS, {**base, 'password': '123', 'confirm_password': '123'}, format='json').status_code == 400
    res = boss_api.post(ACCOUNTS, {**base, 'permissions': ['tickets.view', 'made.up']}, format='json')
    assert res.status_code == 400 and 'made.up' in str(res.data)
    res = boss_api.post(ACCOUNTS, {**base, 'permissions': []}, format='json')   # every box unticked
    assert res.status_code == 201 and res.data['permissions'] == []


def test_edit_permissions_takes_effect_straight_away(boss_api, client_and_site):
    client, site = client_and_site
    ticket = make_ticket(client, site)
    staff = make_staff()   # no row yet: read defaults
    api = api_for(staff)
    assert api.patch(f'/client-portal/staff/tickets/{ticket.pk}/', {'status': 'visit_booked'}, format='json').status_code == 403
    res = boss_api.patch(f'{ACCOUNTS}{staff.pk}/', {'permissions': [*catalogue.DEFAULT_PERMS, 'tickets.edit']}, format='json')
    assert res.status_code == 200 and 'tickets.edit' in res.data['permissions']
    assert api_for(User.objects.get(pk=staff.pk)).patch(
        f'/client-portal/staff/tickets/{ticket.pk}/', {'status': 'visit_booked'}, format='json').status_code == 200


def test_deactivate_rename_and_reset_password(boss_api, boss):
    staff = make_staff()
    res = boss_api.patch(f'{ACCOUNTS}{staff.pk}/', {'name': 'Thandi M', 'password': 'New-Pass-2026x',
                                                    'confirm_password': 'New-Pass-2026x'}, format='json')
    assert res.status_code == 200 and res.data['name'] == 'Thandi M'
    assert login('thandi', 'New-Pass-2026x').status_code == 200
    assert boss_api.patch(f'{ACCOUNTS}{staff.pk}/', {'is_active': False}, format='json').data['is_active'] is False
    assert login('thandi', 'New-Pass-2026x').status_code == 401        # can't sign in any more
    # superusers aren't edited here, and nobody switches themselves off
    assert boss_api.patch(f'{ACCOUNTS}{boss.pk}/', {'is_active': False}, format='json').status_code == 400
    listed = {u['username']: u for u in boss_api.get(ACCOUNTS).data}
    assert listed['boss']['is_superuser'] and listed['boss']['permissions'] == catalogue.ALL_PERMS
    assert listed['thandi']['is_active'] is False


# ── every section checks its permissions ─────────────────────────────────────

def test_tickets_permissions(client_and_site):
    client, site = client_and_site
    service = make_service('Electrical')
    ticket = make_ticket(client, site)
    TicketMessage.objects.create(ticket=ticket, author=client, author_role='client', body='Hello')
    url = f'/client-portal/staff/tickets/{ticket.pk}/'

    nothing = api_for(make_staff('none', perms=[]))
    assert nothing.get('/client-portal/staff/tickets/').status_code == 403
    assert nothing.get(f'{url}messages/').status_code == 403

    reader = api_for(make_staff('reader', perms=catalogue.DEFAULT_PERMS))
    assert reader.get('/client-portal/staff/tickets/').status_code == 200
    assert reader.get(url).status_code == 200
    assert reader.get(f'{url}messages/').status_code == 200
    res = reader.post(f'{url}messages/', {'body': 'Hi'}, format='json')
    assert res.status_code == 403 and 'send chat messages' in res.data['detail']
    assert reader.patch(url, {'technician_name': 'Sam'}, format='json').status_code == 403
    assert reader.delete(url).status_code == 405  # tickets can't be deleted

    worker = api_for(make_staff('worker', perms=['tickets.view', 'tickets.edit', 'tickets.chat_view', 'tickets.chat_send']))
    assert worker.post(f'{url}messages/', {'body': 'On our way'}, format='json').status_code == 201
    assert worker.patch(url, {'status': 'visit_booked'}, format='json').status_code == 200
    res = worker.patch(url, {'status': 'in_progress', 'technician_name': 'Sam'}, format='json')
    assert res.status_code == 403 and 'assign technician' in res.data['detail']

    boss = api_for(make_staff('lead', perms=['tickets.view', 'tickets.assign', 'tickets.create']))
    assert boss.patch(url, {'technician_name': 'Sam'}, format='json').data['technician_name'] == 'Sam'
    res = boss.post('/client-portal/staff/tickets/', {'client': client.pk, 'site': site.pk, 'service': service.pk,
                                                      'title': 'Raised by APS', 'description': 'Phoned in'}, format='json')
    assert res.status_code == 201, res.data
    new = Ticket.objects.get(title='Raised by APS')
    assert new.client == client and res.data['client']['id'] == client.pk
    # another client's site is refused
    from apps.solar_dashboard.models import Site
    theirs = Site.objects.create(client=make_client('other'), name='Theirs')
    assert boss.post('/client-portal/staff/tickets/', {'client': client.pk, 'site': theirs.pk, 'service': service.pk,
                                                       'title': 'x', 'description': 'y'}, format='json').status_code == 400
    assert boss.post('/client-portal/staff/tickets/', {'site': site.pk, 'title': 'x'}, format='json').status_code == 400
    # Tickets can't be deleted by anyone.
    full = api_for(make_staff('full', perms=catalogue.ALL_PERMS))
    assert full.delete(f'/client-portal/staff/tickets/{new.pk}/').status_code == 405
    assert Ticket.objects.filter(pk=new.pk).exists()
    assert 'tickets.delete' not in catalogue.ALL_PERMS


def test_reports_clients_and_sites_permissions(client_and_site):
    client, site = client_and_site
    reader = api_for(make_staff('reader', perms=['reports.view']))
    assert reader.get(reverse('solar-report-list-create')).status_code == 200
    assert reader.get(reverse('client-list')).status_code == 200       # the reports filter needs it
    res = reader.post(reverse('solar-report-list-create'), {}, format='json')
    assert res.status_code == 403 and 'create reports' in res.data['detail']
    assert reader.post(reverse('client-create'), {}, format='json').status_code == 403
    assert reader.patch(f'/dashboard/sites/{site.pk}/', {'name': 'x'}, format='json').status_code == 403

    nothing = api_for(make_staff('none', perms=[]))
    assert nothing.get(reverse('solar-report-list-create')).status_code == 403
    assert nothing.get(reverse('site-list-create')).status_code == 403

    editor = api_for(make_staff('editor', perms=['clients.view', 'clients.edit', 'clients.create']))
    assert editor.patch(f'/dashboard/sites/{site.pk}/', {'name': 'HQ'}, format='json').status_code == 200
    assert editor.delete(f'/dashboard/sites/{site.pk}/').status_code == 403
    assert editor.post(reverse('client-create'), {}, format='json').status_code == 400   # allowed, just invalid
    # clients still see their own reports
    assert api_for(client).get(reverse('solar-report-list-create')).status_code == 200


@pytest.mark.parametrize('section,list_url', [
    ('services', '/services-projects/admin/services/'),
    ('projects', '/services-projects/admin/projects/'),
    ('blogs', '/blogs/admin/posts/'),
    ('blogs', '/blogs/admin/categories/'),
])
def test_site_content_crud_permissions(section, list_url):
    reader = api_for(make_staff('reader', perms=[f'{section}.view']))
    assert reader.get(list_url).status_code == 200
    assert reader.post(list_url, {}, format='json').status_code == 403
    creator = api_for(make_staff('creator', perms=[f'{section}.view', f'{section}.create']))
    assert creator.post(list_url, {}, format='json').status_code == 400   # passes the permission, fails validation
    assert api_for(make_staff('none', perms=[])).get(list_url).status_code == 403


def test_whatsapp_quotes_and_estimates_permissions():
    reader = api_for(make_staff('reader', perms=['whatsapp.view', 'quotes.view', 'estimates.view']))
    assert reader.get(reverse('whatsapp-message-list')).status_code == 200
    res = reader.patch(reverse('whatsapp-message-mark', args=[999]), {'is_job': True}, format='json')
    assert res.status_code == 403 and 'mark messages as jobs' in res.data['detail']
    # with the permission it gets past the check (and finds no such message)
    assert api_for(make_staff('marker', perms=['whatsapp.edit'])).patch(
        reverse('whatsapp-message-mark', args=[999]), {'is_job': True}, format='json').status_code == 404

    assert reader.get('/quote-request/staff/').status_code == 200
    assert api_for(make_staff('none', perms=[])).get('/quote-request/staff/').status_code == 403

    assert reader.get('/solar-estimates/').status_code == 200
    assert reader.get('/solar-estimates/settings/').status_code == 200
    res = reader.put('/solar-estimates/settings/', {'vat_rate': 0.15}, format='json')
    assert res.status_code == 403 and 'prices & settings' in res.data['detail']
    assert reader.post('/solar-estimates/', {'inputs': {}}, format='json').status_code == 403


def test_clients_never_pass_staff_checks(client_and_site):
    client, _ = client_and_site
    api = api_for(client)
    for url in ('/client-portal/staff/tickets/', '/quote-request/staff/', '/solar-estimates/',
                '/services-projects/admin/services/', reverse('client-list')):
        assert api.get(url).status_code == 403, url


def test_existing_staff_keep_full_access_after_the_upgrade():
    """Migration 0002: staff accounts that existed before permissions get them all."""
    import importlib
    migration = importlib.import_module('apps.staff.migrations.0002_keep_existing_staff_access')
    old = make_staff('legacy')
    from django.apps import apps as django_apps
    migration.grant(django_apps, None)
    assert StaffAccess.objects.get(user=old).permissions == migration.ALL_PERMS_AT_LAUNCH


def test_sign_in_records_last_login():
    staff = make_staff('logger', perms=[])
    assert staff.last_login is None
    assert login('logger').status_code == 200
    staff.refresh_from_db()
    assert staff.last_login is not None


def test_existing_staff_get_the_new_trash_permission():
    """Migration 0003: trash.view is a read permission, so existing accounts get it too."""
    import importlib
    migration = importlib.import_module('apps.staff.migrations.0003_trash_view_for_existing_staff')
    old = make_staff('legacy', perms=['reports.view'])
    from django.apps import apps as django_apps
    migration.grant(django_apps, None)
    migration.grant(django_apps, None)  # safe to run twice
    assert StaffAccess.objects.get(user=old).permissions == ['reports.view', 'trash.view']
