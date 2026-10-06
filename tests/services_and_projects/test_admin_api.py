"""
Tests for the dashboard "Site content" editor endpoints for services and
projects (admin-only CRUD under /services-projects/admin/).
"""

import json
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import ClientProfile
from apps.services_and_projects.models import Project, Service

# Smallest valid GIF — ImageField runs it through Pillow.
GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)


def _img(name='pic.gif'):
    return SimpleUploadedFile(name, GIF, content_type='image/gif')


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def client_user(db, user_factory):
    u = user_factory(username='clientguy')
    ClientProfile.objects.create(user=u, role='client', company_name='Acme')
    return u


@pytest.fixture
def service(db):
    return Service.objects.create(
        title='Solar', short_description='s', long_description='l',
        image='services/solar.jpg', features=['A'], appreciation_mark=5,
    )


def _project(service, **kw):
    data = dict(
        service=service, title='House Brown', short_description='s',
        long_description='l', image='projects/p.jpg', location='Wynberg',
        completion_date=date(2026, 5, 1), duration='2 weeks',
    )
    data.update(kw)
    return Project.objects.create(**data)


@pytest.mark.django_db
class TestAccess:

    def test_requires_auth(self, api_client):
        assert api_client.get('/services-projects/admin/services/').status_code == 401

    def test_forbidden_for_client(self, api_client, client_user):
        api_client.force_authenticate(user=client_user)
        assert api_client.get('/services-projects/admin/services/').status_code == 403
        assert api_client.get('/services-projects/admin/projects/').status_code == 403


@pytest.mark.django_db
class TestServiceCrud:

    def test_list_includes_project_count(self, admin_api_client, service):
        _project(service)
        _project(service, title='House Nash')
        res = admin_api_client.get('/services-projects/admin/services/')
        assert res.status_code == 200
        assert res.data[0]['project_count'] == 2

    def test_create_with_image_and_features(self, admin_api_client):
        res = admin_api_client.post('/services-projects/admin/services/', {
            'title': 'EV charging', 'short_description': 's', 'long_description': 'l',
            'features': json.dumps(['Wallbox', '  ', 'Load balancing ']),
            'appreciation_mark': 3, 'image': _img(),
        }, format='multipart')
        assert res.status_code == 201, res.data
        svc = Service.objects.get(id=res.data['id'])
        assert svc.features == ['Wallbox', 'Load balancing']
        assert svc.image.name.startswith('services/')

    def test_create_requires_image(self, admin_api_client):
        res = admin_api_client.post('/services-projects/admin/services/', {
            'title': 'X', 'short_description': 's', 'long_description': 'l',
        }, format='multipart')
        assert res.status_code == 400
        assert 'image' in res.data

    def test_patch_keeps_image_when_not_sent(self, admin_api_client, service):
        res = admin_api_client.patch(f'/services-projects/admin/services/{service.id}/',
                                     {'title': 'Solar PV'}, format='multipart')
        assert res.status_code == 200, res.data
        service.refresh_from_db()
        assert service.title == 'Solar PV'
        assert service.image.name == 'services/solar.jpg'

    def test_rejects_non_list_features(self, admin_api_client, service):
        res = admin_api_client.patch(f'/services-projects/admin/services/{service.id}/',
                                     {'features': json.dumps({'a': 1})}, format='multipart')
        assert res.status_code == 400

    def test_delete_cascades_projects(self, admin_api_client, service):
        _project(service)
        res = admin_api_client.delete(f'/services-projects/admin/services/{service.id}/')
        assert res.status_code == 204
        assert not Service.objects.exists()
        assert not Project.objects.exists()


@pytest.mark.django_db
class TestProjectCrud:

    def test_list_filters_by_service(self, admin_api_client, service):
        other = Service.objects.create(title='Plumbing', short_description='s',
                                       long_description='l', image='services/p.jpg')
        _project(service)
        _project(other, title='Geyser swap')
        res = admin_api_client.get(f'/services-projects/admin/projects/?service={other.id}')
        assert [p['title'] for p in res.data] == ['Geyser swap']
        assert res.data[0]['service_title'] == 'Plumbing'

    def test_create(self, admin_api_client, service):
        res = admin_api_client.post('/services-projects/admin/projects/', {
            'service': service.id, 'title': 'House Nash', 'short_description': 's',
            'long_description': 'l', 'location': 'Constantia',
            'completion_date': '2026-08-01', 'duration': '3 days',
            'features': json.dumps(['5 kW Sunsynk']), 'image': _img(), 'image_2': _img('b.gif'),
        }, format='multipart')
        assert res.status_code == 201, res.data
        p = Project.objects.get(id=res.data['id'])
        assert p.image_2
        assert not p.image_3

    def test_remove_optional_image(self, admin_api_client, service):
        p = _project(service, image_2='projects/two.jpg')
        res = admin_api_client.patch(f'/services-projects/admin/projects/{p.id}/',
                                     {'remove_image_2': 'true'}, format='multipart')
        assert res.status_code == 200, res.data
        p.refresh_from_db()
        assert not p.image_2
        assert p.image.name == 'projects/p.jpg'

    def test_delete(self, admin_api_client, service):
        p = _project(service)
        assert admin_api_client.delete(f'/services-projects/admin/projects/{p.id}/').status_code == 204
        assert Service.objects.filter(id=service.id).exists()


@pytest.mark.django_db(transaction=True)
class TestImageHousekeeping:

    def test_oversized_image_rejected(self, admin_api_client, monkeypatch):
        monkeypatch.setattr('apps.core.content_files.MAX_CONTENT_IMAGE_BYTES', 10)
        res = admin_api_client.post('/services-projects/admin/services/', {
            'title': 'Big', 'short_description': 's', 'long_description': 'l', 'image': _img(),
        }, format='multipart')
        assert res.status_code == 400
        assert 'image' in res.data

    def test_replaced_and_deleted_files_are_removed(self, admin_api_client, settings):
        res = admin_api_client.post('/services-projects/admin/services/', {
            'title': 'Solar', 'short_description': 's', 'long_description': 'l', 'image': _img('one.gif'),
        }, format='multipart')
        svc = Service.objects.get(id=res.data['id'])
        first = svc.image.path
        admin_api_client.patch(f'/services-projects/admin/services/{svc.id}/', {'image': _img('two.gif')},
                               format='multipart')
        svc.refresh_from_db()
        second = svc.image.path
        import os
        assert not os.path.exists(first) and os.path.exists(second)
        admin_api_client.delete(f'/services-projects/admin/services/{svc.id}/')
        assert not os.path.exists(second)
