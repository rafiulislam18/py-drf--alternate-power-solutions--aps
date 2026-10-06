"""
Tests for the client dashboard API (apps.client_portal).

Covers the client-only gate (anonymous 401, staff 403, a real dashboard token
works), client scoping (a client never sees or targets another client's
tickets or sites), the Sites page (list with counts, add, rename, no delete),
ticket list filters / search / sort / pagination, the status summary, and
ticket creation with attachment validation and the notification emails.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.client_portal.alerts import send_new_ticket_alerts
from apps.client_portal.models import Ticket, TicketAttachment
from apps.solar_dashboard.models import SiteData, Site, SolarReport

from .conftest import api_for, bearer, login, make_client, make_service, make_ticket

ME_URL = '/client-portal/me/'
SUMMARY_URL = '/client-portal/tickets/summary/'
TICKETS_URL = '/client-portal/tickets/'
SITES_URL = '/client-portal/sites/'
SUBS_URL = '/client-portal/subscriptions/'
CLIENT_URLS = [ME_URL, SUMMARY_URL, TICKETS_URL, SITES_URL, SUBS_URL,
               '/client-portal/subscriptions/payments/']

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
PDF = b'%PDF-1.4\n' + b'\x00' * 32
JPG = b'\xff\xd8\xff\xe0' + b'\x00' * 32


def _payload(site, **kw):
    data = {
        'service': make_service('Electrical').pk,
        'title': 'DB board tripping in block B',
        'description': 'The main breaker trips every time the aircon starts.',
        'site': site.pk,
        'urgency': 'normal',
    }
    data.update(kw)
    return data


# ── who may use it ──────────────────────────────────────────────────────────


@pytest.mark.parametrize('url', CLIENT_URLS)
def test_endpoints_require_sign_in(db, url):
    assert api_for().get(url).status_code == 401


def test_writes_require_sign_in(db):
    assert api_for().post(SITES_URL, {'name': 'X'}, format='json').status_code == 401
    assert api_for().post(TICKETS_URL, {}, format='json').status_code == 401


@pytest.mark.parametrize('url', CLIENT_URLS)
def test_staff_are_refused(staff_user, url):
    assert api_for(staff_user).get(url).status_code == 403


def test_superuser_refused_even_on_writes(admin_user, site):
    api = api_for(admin_user)
    assert api.post(SITES_URL, {'name': 'Sneaky'}, format='json').status_code == 403
    assert api.post(TICKETS_URL, _payload(site), format='json').status_code == 403
    assert not Ticket.objects.exists()


def test_admin_role_profile_is_refused(db):
    # A non-staff login given the dashboard admin role is staff for these purposes.
    from apps.core.models import ClientProfile

    user = make_client('aps-office')
    ClientProfile.objects.filter(user=user).update(role='admin')
    user.refresh_from_db()
    assert api_for(user).get(ME_URL).status_code == 403


def test_real_dashboard_token_works(client_user):
    res = login('sipho@acme.example')
    assert res.status_code == 200, res.data
    assert res.data['role'] == 'client'
    assert bearer(res.data['access']).get(ME_URL).status_code == 200


def test_garbage_token_rejected(db):
    assert bearer('not-a-token').get(ME_URL).status_code == 401


def test_deactivated_client_token_stops_working(client_user):
    api = bearer(login('acme').data['access'])
    assert api.get(ME_URL).status_code == 200
    client_user.is_active = False
    client_user.save()
    assert api.get(ME_URL).status_code == 401


def test_auth_errors_stay_flat(db):
    res = api_for().get(SITES_URL)
    assert res.status_code == 401
    assert set(res.data) == {'detail'}


def test_me_returns_client(api, client_user):
    res = api.get(ME_URL)
    assert res.status_code == 200
    assert res.data['contact'] == {'name': 'Acme Properties', 'email': 'sipho@acme.example', 'phone': '021 555 0100'}
    assert res.data['company'] == {'name': 'Acme Properties'}
    assert res.data['email_verified'] is True
    solar = make_service('Solar')
    services = api.get(ME_URL).data['services']
    assert any(s['id'] == solar.pk and s['title'] == 'Solar' for s in services)
    assert 'sites' not in res.data  # sites come from their own endpoint


def test_me_falls_back_to_username_and_reports_unverified(db):
    user = make_client('plainco', email='x@plain.example', verified=False)
    res = api_for(user).get(ME_URL)
    assert res.data['company']['name'] == 'plainco'
    assert res.data['email_verified'] is False


# ── sites ───────────────────────────────────────────────────────────────────


def test_sites_list_active_own_sites_with_counts(client_user, api, site, warehouse, other_site):
    retired = Site.objects.create(client=client_user, name='Old depot', is_active=False)
    make_ticket(client_user, site, status=Ticket.Status.OPEN)
    make_ticket(client_user, site, status=Ticket.Status.ON_SITE)
    make_ticket(client_user, site, status=Ticket.Status.COMPLETED)
    make_ticket(client_user, retired)
    for month in (6, 7):
        report = SolarReport.objects.create(client=client_user, report_date=date(2026, month, 28),
                                            period_start=date(2026, month, 1), period_end=date(2026, month, 28))
        SiteData.objects.create(report=report, site=site)

    res = api.get(SITES_URL)
    assert res.status_code == 200
    by_name = {s['name']: s for s in res.data}
    assert set(by_name) == {'Head office', 'Warehouse'}
    head = by_name['Head office']
    # Three tickets and two reports on one site: the joins mustn't multiply.
    assert (head['ticket_count'], head['open_ticket_count'], head['report_count']) == (3, 2, 2)
    assert (by_name['Warehouse']['ticket_count'], by_name['Warehouse']['open_ticket_count'],
            by_name['Warehouse']['report_count']) == (0, 0, 0)
    assert head['created_by_name'] == 'You'
    assert by_name['Warehouse']['created_by_name'] == 'APS'


def test_site_added_by_staff_shows_as_aps(client_user, api, staff_user):
    Site.objects.create(client=client_user, name='Block D', created_by=staff_user)
    assert api.get(SITES_URL).data[0]['created_by_name'] == 'APS'


def test_create_site(client_user, api):
    res = api.post(SITES_URL, {'name': '  Block   C  ', 'address': ' 1  Main Rd '}, format='json')
    assert res.status_code == 201, res.data
    assert res.data['name'] == 'Block C'
    assert res.data['address'] == '1 Main Rd'
    assert res.data['created_by_name'] == 'You'
    assert res.data['ticket_count'] == 0
    created = Site.objects.get(pk=res.data['id'])
    assert created.client == client_user
    assert created.created_by == client_user
    assert created.is_active

    assert 'Block C' in [s['name'] for s in api.get(SITES_URL).data]


def test_new_site_usable_for_ticket_straight_away(api):
    site_id = api.post(SITES_URL, {'name': 'Block C'}, format='json').data['id']
    res = api.post(TICKETS_URL, _payload(Site.objects.get(pk=site_id)), format='multipart')
    assert res.status_code == 201
    assert res.data['site']['name'] == 'Block C'


def test_create_site_ignores_client_supplied_owner(client_user, api, other_client):
    res = api.post(SITES_URL, {'name': 'Mine', 'client': other_client.pk, 'client_id': other_client.pk,
                               'created_by': other_client.pk, 'is_active': False}, format='json')
    assert res.status_code == 201
    created = Site.objects.get(pk=res.data['id'])
    assert created.client == client_user
    assert created.created_by == client_user
    assert created.is_active


@pytest.mark.parametrize('name', ['', '   '])
def test_create_site_requires_name(api, name):
    res = api.post(SITES_URL, {'name': name}, format='json')
    assert res.status_code == 400
    assert 'name' in res.data


def test_create_site_rejects_duplicate_name_any_case(api, site):
    res = api.post(SITES_URL, {'name': 'HEAD OFFICE'}, format='json')
    assert res.status_code == 400
    assert 'already have' in res.data['name'][0]
    # Field errors keep their field name AND carry a detail.
    assert 'already have' in res.data['detail']


def test_create_site_rejects_retired_name(client_user, api):
    Site.objects.create(client=client_user, name='Old depot', is_active=False)
    res = api.post(SITES_URL, {'name': 'old depot'}, format='json')
    assert res.status_code == 400
    assert 'retired' in res.data['name'][0]


def test_same_site_name_allowed_for_another_client(api, other_site):
    res = api.post(SITES_URL, {'name': other_site.name}, format='json')
    assert res.status_code == 201


def test_edit_site(api, site, warehouse):
    res = api.patch(f'{SITES_URL}{site.pk}/', {'address': '5 Long St'}, format='json')
    assert res.status_code == 200
    assert res.data['name'] == 'Head office'
    assert res.data['address'] == '5 Long St'

    # Renaming to its own name in another case is fine; to a sibling's is not.
    assert api.patch(f'{SITES_URL}{site.pk}/', {'name': 'HEAD Office'}, format='json').status_code == 200
    clash = api.patch(f'{SITES_URL}{site.pk}/', {'name': 'warehouse'}, format='json')
    assert clash.status_code == 400
    assert 'name' in clash.data


def test_edit_site_cannot_retire_or_move_it(client_user, api, site, other_client):
    res = api.patch(f'{SITES_URL}{site.pk}/', {'is_active': False, 'client': other_client.pk,
                                               'has_battery': True}, format='json')
    assert res.status_code == 200
    site.refresh_from_db()
    assert site.is_active and site.client == client_user and not site.has_battery


def test_cannot_edit_other_clients_or_retired_site(api, site, other_site):
    assert api.patch(f'{SITES_URL}{other_site.pk}/', {'name': 'Hijack'}, format='json').status_code == 404
    site.is_active = False
    site.save()
    assert api.patch(f'{SITES_URL}{site.pk}/', {'name': 'Back'}, format='json').status_code == 404
    other_site.refresh_from_db()
    assert other_site.name == 'Their site'


@pytest.mark.parametrize('method', ['delete', 'put', 'get'])
def test_site_detail_is_patch_only(api, site, method):
    res = getattr(api, method)(f'{SITES_URL}{site.pk}/', {'name': 'X'}, format='json')
    assert res.status_code == 405
    assert Site.objects.filter(pk=site.pk, name='Head office').exists()


def test_site_writes_are_throttled_but_browsing_is_not(api, site):
    for i in range(30):
        assert api.patch(f'{SITES_URL}{site.pk}/', {'address': f'{i} Main Rd'}, format='json').status_code == 200
    assert api.patch(f'{SITES_URL}{site.pk}/', {'address': 'x'}, format='json').status_code == 429
    assert api.post(SITES_URL, {'name': 'Another'}, format='json').status_code == 429
    assert api.get(SITES_URL).status_code == 200


def test_site_throttle_is_per_client(api, site, other_client):
    for i in range(30):
        api.patch(f'{SITES_URL}{site.pk}/', {'address': f'{i}'}, format='json')
    assert api_for(other_client).post(SITES_URL, {'name': 'Fine'}, format='json').status_code == 201


# ── list ────────────────────────────────────────────────────────────────────


def test_list_is_client_scoped(client_user, api, site, other_client, other_site):
    mine = make_ticket(client_user, site, title='Mine')
    make_ticket(other_client, other_site, title='Theirs')
    res = api.get(TICKETS_URL)
    assert res.status_code == 200
    assert res.data['count'] == 1
    assert res.data['results'][0]['reference'] == mine.reference


def test_list_row_shape(client_user, api, site):
    ticket = make_ticket(client_user, site, status=Ticket.Status.QUOTE_TO_APPROVE, technician_name='Riaan')
    row = api.get(TICKETS_URL).data['results'][0]
    assert row['reference'] == ticket.reference
    assert row['status_group'] == 'waiting'
    assert row['status_label'] == 'Quote to approve'
    assert row['site'] == {'id': site.pk, 'name': 'Head office', 'address': ''}
    assert row['technician_name'] == 'Riaan'
    assert row['attachment_count'] == 0
    assert 'client' not in row


def test_reference_format(client_user, site):
    ticket = make_ticket(client_user, site)
    assert ticket.reference == f'APS-{1000 + ticket.pk}'
    assert Ticket.pk_from_reference(ticket.reference) == ticket.pk
    assert Ticket.pk_from_reference(f'aps{1000 + ticket.pk}') == ticket.pk
    assert Ticket.pk_from_reference(str(1000 + ticket.pk)) == ticket.pk
    assert Ticket.pk_from_reference('APS-999') is None
    assert Ticket.pk_from_reference('leak') is None


def test_list_filters(client_user, api, site, warehouse):
    make_ticket(client_user, site, status=Ticket.Status.OPEN, service='solar')
    make_ticket(client_user, site, status=Ticket.Status.ON_SITE)
    make_ticket(client_user, warehouse, status=Ticket.Status.VISIT_BOOKED, urgency='urgent')
    make_ticket(client_user, warehouse, status=Ticket.Status.QUOTE_TO_APPROVE)
    make_ticket(client_user, site, status=Ticket.Status.COMPLETED)

    def count(**params):
        return api.get(TICKETS_URL, params).data['count']

    assert count() == 5
    assert count(status_group='open') == 1
    assert count(status_group='in_progress') == 2
    assert count(status_group='waiting') == 1
    assert count(status_group='completed') == 1
    assert count(status_group='bogus') == 5
    assert count(service='Solar') == 1
    assert count(service='Nope') == 0
    assert count(urgency='urgent') == 1
    assert count(site=warehouse.pk) == 2
    assert count(site=warehouse.pk, status_group='waiting') == 1


def test_list_site_filter_cannot_reach_other_clients_site(client_user, api, other_client, other_site):
    make_ticket(other_client, other_site)
    assert api.get(TICKETS_URL, {'site': other_site.pk}).data['count'] == 0


def test_list_search(client_user, api, site, warehouse):
    leak = make_ticket(client_user, site, title='Roof leak above office 3')
    make_ticket(client_user, warehouse, title='Inverter fault', technician_name='Riaan V.')
    make_ticket(client_user, site, title='Geyser', description='Drips onto the ceiling boards')

    def refs(q):
        return [t['reference'] for t in api.get(TICKETS_URL, {'search': q}).data['results']]

    assert refs('roof leak') == [leak.reference]
    assert refs(leak.reference) == [leak.reference]
    assert refs(leak.reference.lower().replace('-', '')) == [leak.reference]
    assert len(refs('warehouse')) == 1
    assert len(refs('riaan')) == 1
    assert len(refs('ceiling')) == 1
    assert refs('nothing like this') == []


def test_list_search_by_reference_stays_client_scoped(api, site, other_client, other_site):
    theirs = make_ticket(other_client, other_site)
    res = api.get(TICKETS_URL, {'search': theirs.reference})
    assert res.data['count'] == 0


def test_list_ordering(client_user, api, site, warehouse):
    a = make_ticket(client_user, site, title='Alpha')
    b = make_ticket(client_user, warehouse, title='Charlie')
    c = make_ticket(client_user, site, title='Bravo')

    def titles(ordering):
        return [t['title'] for t in api.get(TICKETS_URL, {'ordering': ordering}).data['results']]

    assert titles('title') == ['Alpha', 'Bravo', 'Charlie']
    assert titles('-title') == ['Charlie', 'Bravo', 'Alpha']
    assert titles('reference') == [a.title, b.title, c.title]
    assert titles('-reference') == [c.title, b.title, a.title]
    assert titles('site') == ['Alpha', 'Bravo', 'Charlie']  # Head office ×2 (by id), then Warehouse
    # Default: most recently updated first.
    Ticket.objects.filter(pk=a.pk).update(updated_at=timezone.now() + timedelta(minutes=5))
    assert titles('')[0] == 'Alpha'
    assert titles('description')[0] == 'Alpha'  # not a sortable field → default


def test_list_pagination(client_user, api, site):
    for i in range(23):
        make_ticket(client_user, site, title=f'Job {i}')

    first = api.get(TICKETS_URL).data
    assert first['count'] == 23
    assert first['total_pages'] == 3
    assert first['page'] == 1
    assert len(first['results']) == 10

    last = api.get(TICKETS_URL, {'page': 3}).data
    assert len(last['results']) == 3

    big = api.get(TICKETS_URL, {'page_size': 500}).data
    assert big['page_size'] == 50
    assert len(big['results']) == 23

    assert api.get(TICKETS_URL, {'page': 9}).status_code == 404


def test_summary_counts(client_user, api, site, other_client, other_site):
    make_ticket(client_user, site, status=Ticket.Status.OPEN)
    make_ticket(client_user, site, status=Ticket.Status.IN_PROGRESS)
    make_ticket(client_user, site, status=Ticket.Status.ON_SITE)
    make_ticket(client_user, site, status=Ticket.Status.INFO_NEEDED)
    make_ticket(client_user, site, status=Ticket.Status.CANCELLED)
    make_ticket(other_client, other_site, status=Ticket.Status.OPEN)

    res = api.get(SUMMARY_URL)
    assert res.data == {'open': 1, 'in_progress': 2, 'waiting': 1, 'completed': 1, 'all': 5}


def test_summary_empty(api):
    assert api.get(SUMMARY_URL).data == {'open': 0, 'in_progress': 0, 'waiting': 0, 'completed': 0, 'all': 0}


# ── create ──────────────────────────────────────────────────────────────────


def test_create_ticket(client_user, api, site, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        res = api.post(
            TICKETS_URL,
            _payload(
                site,
                preferred_visit_date=(timezone.localdate() + timedelta(days=2)).isoformat(),
                site_contact_name='Security desk',
                site_contact_phone='082 123 4567',
                files=[
                    SimpleUploadedFile('photo.png', PNG, content_type='image/png'),
                    SimpleUploadedFile('quote.pdf', PDF, content_type='application/pdf'),
                    SimpleUploadedFile('meter.JPG', JPG, content_type='image/jpeg'),
                ],
            ),
            format='multipart',
        )
    assert res.status_code == 201, res.data
    ticket = Ticket.objects.get()
    assert ticket.client == client_user
    assert ticket.site == site
    assert ticket.status == Ticket.Status.OPEN
    assert res.data['reference'] == ticket.reference
    assert res.data['attachment_count'] == 3
    assert set(TicketAttachment.objects.values_list('original_name', 'content_type')) == {
        ('photo.png', 'image/png'), ('quote.pdf', 'application/pdf'), ('meter.JPG', 'image/jpeg'),
    }
    stored = TicketAttachment.objects.first().file.name
    assert 'photo' not in stored and 'quote' not in stored  # random names

    # Not an emergency: nothing is emailed until the 30-minute job runs.
    assert mail.outbox == []
    assert ticket.team_alert_pending and ticket.client_alert_pending
    send_new_ticket_alerts()

    team = [m for m in mail.outbox if m.to == ['admin@example.com']]
    assert len(team) == 1
    assert team[0].subject.startswith('[Normal] New ticket') and ticket.reference in team[0].subject
    assert 'Acme Properties' in team[0].subject
    assert 'Security desk' in team[0].body
    assert (timezone.localdate() + timedelta(days=2)).strftime('%d/%m/%Y') in team[0].body

    confirmation = [m for m in mail.outbox if m.to == ['sipho@acme.example']]
    assert len(confirmation) == 1
    assert confirmation[0].subject == f'We received your ticket {ticket.reference}'


def test_no_confirmation_to_unverified_email(site, django_capture_on_commit_callbacks):
    user = make_client('plainco', email='maybe@plain.example', verified=False)
    their_site = Site.objects.create(client=user, name='Only site')
    with django_capture_on_commit_callbacks(execute=True):
        res = api_for(user).post(TICKETS_URL, _payload(their_site), format='multipart')
    assert res.status_code == 201
    send_new_ticket_alerts()
    assert [m.to for m in mail.outbox] == [['admin@example.com']]
    assert not Ticket.objects.get().client_alert_pending  # nothing to send, so not retried


def test_create_escapes_user_text_in_email(api, site, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        api.post(
            TICKETS_URL,
            _payload(site, title='<script>x</script>', description='<img src=x onerror=alert(1)>',
                     site_contact_name='<b>Bob</b>'),
            format='multipart',
        )
    send_new_ticket_alerts()
    team = next(m for m in mail.outbox if 'New ticket' in m.subject)
    assert '<script>' not in team.body
    assert '<img' not in team.body
    assert '<b>Bob' not in team.body
    assert '&lt;script&gt;' in team.body


def test_create_emergency_flags_team_email(api, site, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        api.post(TICKETS_URL, _payload(site, urgency='emergency'), format='multipart')
    # Emergencies don't wait for the 30-minute job.
    ticket = Ticket.objects.get()
    assert not ticket.team_alert_pending and not ticket.client_alert_pending
    team = next(m for m in mail.outbox if 'New ticket' in m.subject)
    assert team.subject.startswith('[Emergency]')
    assert 'EMERGENCY' in team.body
    confirmation = next(m for m in mail.outbox if m.subject.startswith('We received'))
    assert 'emergency' in confirmation.body


def test_emergency_pings_telegram_when_enabled(api, site, settings, django_capture_on_commit_callbacks):
    settings.ALERT_TELEGRAM_ENABLED = True
    settings.TELEGRAM_BOT_TOKEN = 'bot-token'
    settings.TELEGRAM_CHAT_ID = '42'
    with patch('apps.client_portal.emails.requests.post') as post:
        with django_capture_on_commit_callbacks(execute=True):
            api.post(TICKETS_URL, _payload(site, urgency='emergency', title='<Sparks>'), format='multipart')
            api.post(TICKETS_URL, _payload(site, urgency='urgent'), format='multipart')
    assert post.call_count == 1  # only the emergency
    url = post.call_args.args[0]
    body = post.call_args.kwargs['json']
    assert url == 'https://api.telegram.org/botbot-token/sendMessage'
    assert body['chat_id'] == '42'
    assert 'Acme Properties' in body['text']
    assert '&lt;Sparks&gt;' in body['text']
    assert '021 555 0100' in body['text']


def test_telegram_failure_does_not_break_ticket(api, site, settings, django_capture_on_commit_callbacks):
    settings.ALERT_TELEGRAM_ENABLED = True
    settings.TELEGRAM_BOT_TOKEN = 'bot-token'
    settings.TELEGRAM_CHAT_ID = '42'
    with patch('apps.client_portal.emails.requests.post', side_effect=OSError('down')):
        with django_capture_on_commit_callbacks(execute=True):
            res = api.post(TICKETS_URL, _payload(site, urgency='emergency'), format='multipart')
    assert res.status_code == 201
    assert Ticket.objects.count() == 1


def test_create_rejects_other_clients_site(api, other_site):
    res = api.post(TICKETS_URL, _payload(other_site), format='multipart')
    assert res.status_code == 400
    assert 'site' in res.data
    assert not Ticket.objects.exists()


def test_create_rejects_retired_site(api, site):
    site.is_active = False
    site.save()
    res = api.post(TICKETS_URL, _payload(site), format='multipart')
    assert res.status_code == 400
    assert 'site' in res.data


def test_create_rejects_past_visit_date(api, site):
    past = (timezone.localdate() - timedelta(days=1)).isoformat()
    res = api.post(TICKETS_URL, _payload(site, preferred_visit_date=past), format='multipart')
    assert res.status_code == 400
    assert 'preferred_visit_date' in res.data


def test_create_accepts_today_as_visit_date(api, site):
    res = api.post(TICKETS_URL, _payload(site, preferred_visit_date=timezone.localdate().isoformat()),
                   format='multipart')
    assert res.status_code == 201, res.data


@pytest.mark.parametrize('field', ['title', 'description', 'service', 'site'])
def test_create_requires_core_fields(api, site, field):
    data = _payload(site)
    data[field] = ''
    res = api.post(TICKETS_URL, data, format='multipart')
    assert res.status_code == 400
    assert field in res.data


def test_ticket_field_errors_keep_field_names(api, site):
    res = api.post(TICKETS_URL, _payload(site, title='', description=''), format='multipart')
    assert res.status_code == 400
    assert {'title', 'description', 'detail'} <= set(res.data)


def test_create_accepts_json_without_files(api, site):
    res = api.post(TICKETS_URL, _payload(site), format='json')
    assert res.status_code == 201
    assert res.data['urgency'] == 'normal'


def test_create_defaults_urgency_to_normal(api, site):
    data = _payload(site)
    del data['urgency']
    res = api.post(TICKETS_URL, data, format='multipart')
    assert res.status_code == 201
    assert res.data['urgency'] == 'normal'


@pytest.mark.parametrize('upload', [
    SimpleUploadedFile('virus.exe', b'MZ' + b'\x00' * 20),
    SimpleUploadedFile('fake.png', b'MZ' + b'\x00' * 20),   # renamed, wrong bytes
    SimpleUploadedFile('fake.pdf', PNG),
    SimpleUploadedFile('fake.jpg', PDF),
    SimpleUploadedFile('noext', PNG),
])
def test_create_rejects_bad_attachments(api, site, upload):
    res = api.post(TICKETS_URL, _payload(site, files=[upload]), format='multipart')
    assert res.status_code == 400
    assert 'files' in res.data
    assert not Ticket.objects.exists()


def test_create_rejects_too_many_files(api, site):
    files = [SimpleUploadedFile(f'p{i}.png', PNG) for i in range(11)]
    res = api.post(TICKETS_URL, _payload(site, files=files), format='multipart')
    assert res.status_code == 400
    assert 'files' in res.data
    assert not Ticket.objects.exists()


def test_create_rejects_oversized_file(api, site, monkeypatch):
    from apps.client_portal import serializers

    monkeypatch.setattr(serializers, 'MAX_ATTACHMENT_BYTES', 100)
    big = SimpleUploadedFile('big.png', PNG + b'\x00' * 200)
    res = api.post(TICKETS_URL, _payload(site, files=[big]), format='multipart')
    assert res.status_code == 400
    assert 'larger' in str(res.data['files'])
    assert not Ticket.objects.exists()


def test_create_ignores_client_supplied_owner_and_status(client_user, api, site, other_client):
    res = api.post(
        TICKETS_URL,
        _payload(site, client=other_client.pk, status='completed', technician_name='Me'),
        format='multipart',
    )
    assert res.status_code == 201
    ticket = Ticket.objects.get()
    assert ticket.client == client_user
    assert ticket.status == Ticket.Status.OPEN
    assert ticket.technician_name == ''


def test_created_ticket_is_invisible_to_other_client(api, site, other_client):
    assert api.post(TICKETS_URL, _payload(site), format='multipart').status_code == 201
    other = api_for(other_client)
    assert other.get(TICKETS_URL).data['count'] == 0
    assert other.get(SUMMARY_URL).data['all'] == 0
