"""
Solar reports and the trash (apps.records): deleting a report moves it to the
trash — gone from every list, link and total until it's restored — and every
create / edit / delete / restore is in the audit history.
"""

import pytest

from apps.core.models import ClientProfile
from apps.records.models import AuditLog
from apps.solar_dashboard.models import SiteData, Site, SolarReport

TYPE = 'solar_dashboard.solarreport'


@pytest.fixture
def client_user(db, user_factory):
    u = user_factory(username='sunco')
    ClientProfile.objects.create(user=u, role='client', company_name='SunCo')
    return u


@pytest.fixture
def site(client_user):
    return Site.objects.create(client=client_user, name='Alpha')


def _payload(client_id, site_id, start='2026-07-01', end='2026-07-31', solar='120.00'):
    return {'client_id': client_id, 'report_date': end, 'period_start': start, 'period_end': end,
            'sites': [{'site_id': site_id, 'solar_yield': solar}]}


@pytest.mark.django_db
class TestReportTrash:

    def test_delete_moves_to_trash_and_hides_everywhere(self, admin_api_client, admin_user, client_user, site):
        uuid = admin_api_client.post('/dashboard/reports/', _payload(client_user.id, site.id), format='json').data['uuid']

        assert admin_api_client.delete(f'/dashboard/reports/{uuid}/').status_code == 204
        report = SolarReport.all_objects.get(uuid=uuid)
        assert report.in_trash and report.deleted_by == admin_user
        assert SiteData.objects.filter(report=report).exists()  # figures kept for a restore

        assert admin_api_client.get('/dashboard/reports/').data['count'] == 0
        assert admin_api_client.get(f'/dashboard/reports/{uuid}/').status_code == 404  # share link
        res = admin_api_client.get('/records/trash/')
        assert [(i['type_label'], i['name']) for i in res.data['results']] == \
            [('Solar report', 'SunCo — 01/07/2026 to 31/07/2026')]

    def test_restore_brings_it_back(self, admin_api_client, client_user, site):
        uuid = admin_api_client.post('/dashboard/reports/', _payload(client_user.id, site.id), format='json').data['uuid']
        admin_api_client.delete(f'/dashboard/reports/{uuid}/')
        pk = SolarReport.all_objects.get(uuid=uuid).pk

        assert admin_api_client.post(f'/records/trash/{TYPE}/{pk}/restore/').status_code == 200
        assert admin_api_client.get(f'/dashboard/reports/{uuid}/').status_code == 200

    def test_restore_refused_when_a_newer_report_has_the_period(self, admin_api_client, client_user, site):
        old = admin_api_client.post('/dashboard/reports/', _payload(client_user.id, site.id), format='json').data['uuid']
        admin_api_client.delete(f'/dashboard/reports/{old}/')
        # The period is free again, so a replacement can be made…
        res = admin_api_client.post('/dashboard/reports/', _payload(client_user.id, site.id, solar='130.00'), format='json')
        assert res.status_code == 201
        # …and then the old one can't come back on top of it.
        pk = SolarReport.all_objects.get(uuid=old).pk
        res = admin_api_client.post(f'/records/trash/{TYPE}/{pk}/restore/')
        assert res.status_code == 400 and 'already has a newer report' in res.data['detail']
        assert SolarReport.all_objects.get(uuid=old).in_trash

    def test_create_edit_delete_are_in_the_audit_history(self, admin_api_client, admin_user, client_user, site):
        uuid = admin_api_client.post('/dashboard/reports/', _payload(client_user.id, site.id), format='json').data['uuid']
        admin_api_client.put(f'/dashboard/reports/{uuid}/',
                             _payload(client_user.id, site.id, end='2026-07-30', solar='150.00'), format='json')
        admin_api_client.delete(f'/dashboard/reports/{uuid}/')

        entries = list(AuditLog.objects.filter(object_type=TYPE).order_by('id'))
        assert [e.action for e in entries] == ['created', 'updated', 'deleted']
        assert all(e.actor == admin_user and e.section == 'reports' for e in entries)
        edit = entries[1]
        assert edit.changes['period_end'] == {'label': 'Period end', 'old': '31 Jul 2026', 'new': '30 Jul 2026'}
        assert edit.summary == 'Site figures changed'

    def test_edit_without_changes_logs_nothing(self, admin_api_client, client_user, site):
        uuid = admin_api_client.post('/dashboard/reports/', _payload(client_user.id, site.id), format='json').data['uuid']
        admin_api_client.put(f'/dashboard/reports/{uuid}/', _payload(client_user.id, site.id), format='json')
        assert list(AuditLog.objects.values_list('action', flat=True)) == ['created']

    def test_staff_without_delete_permission_cannot_trash(self, api_client, client_user, site):
        from django.contrib.auth.models import User
        from apps.staff.models import StaffAccess
        reader = User.objects.create_user('reader', password='x')
        ClientProfile.objects.create(user=reader, role='admin')
        StaffAccess.objects.create(user=reader, permissions=['reports.view'])
        report = SolarReport.objects.create(client=client_user, report_date='2026-07-31',
                                            period_start='2026-07-01', period_end='2026-07-31')
        api_client.force_authenticate(reader)
        assert api_client.delete(f'/dashboard/reports/{report.uuid}/').status_code == 403
        assert not SolarReport.all_objects.get(pk=report.pk).in_trash


@pytest.mark.django_db
def test_trash_preview_shows_the_sites_and_totals(admin_api_client, client_user, site):
    uuid = admin_api_client.post('/dashboard/reports/', _payload(client_user.id, site.id), format='json').data['uuid']
    admin_api_client.delete(f'/dashboard/reports/{uuid}/')
    pk = SolarReport.all_objects.get(uuid=uuid).pk
    detail = admin_api_client.get(f'/records/trash/{TYPE}/{pk}/').data['detail']
    assert detail['kind'] == 'solar_report' and detail['client'] == 'SunCo'
    assert [s['name'] for s in detail['sites']] == ['Alpha'] and detail['totals']['solar_yield'] == 120.0
