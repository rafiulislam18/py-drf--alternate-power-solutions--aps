"""
Tests for the dashboard "Site content" editor endpoints for blog posts and
categories (admin-only CRUD under /blogs/admin/).
"""

from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.blog.models import Blog, BlogCategory
from apps.core.models import ClientProfile

GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def category(db):
    return BlogCategory.objects.create(name='Solar tips')


def _blog(category, **kw):
    data = dict(
        category=category, title='Load-shedding prep', short_description='s',
        long_description='l', image='blogs/b.jpg', author='APS',
        date=date(2026, 9, 1), read_time='4 min read',
    )
    data.update(kw)
    return Blog.objects.create(**data)


@pytest.mark.django_db
class TestBlogAdmin:

    def test_forbidden_for_client(self, api_client, user_factory):
        u = user_factory(username='clientguy')
        ClientProfile.objects.create(user=u, role='client', company_name='Acme')
        api_client.force_authenticate(user=u)
        assert api_client.get('/blogs/admin/posts/').status_code == 403
        assert api_client.get('/blogs/admin/categories/').status_code == 403

    def test_list_newest_first_with_category_name(self, admin_api_client, category):
        _blog(category, title='Old', date=date(2026, 1, 1))
        _blog(category, title='New', date=date(2026, 9, 1))
        res = admin_api_client.get('/blogs/admin/posts/')
        assert [b['title'] for b in res.data] == ['New', 'Old']
        assert res.data[0]['category_name'] == 'Solar tips'

    def test_create_and_update(self, admin_api_client, category):
        res = admin_api_client.post('/blogs/admin/posts/', {
            'category': category.id, 'title': 'Inverter care', 'short_description': 's',
            'long_description': 'l', 'author': 'APS', 'date': '2026-09-28',
            'read_time': '3 min read',
            'image': SimpleUploadedFile('c.gif', GIF, content_type='image/gif'),
        }, format='multipart')
        assert res.status_code == 201, res.data
        blog_id = res.data['id']
        res = admin_api_client.patch(f'/blogs/admin/posts/{blog_id}/', {'title': 'Inverter care 101'},
                                     format='multipart')
        assert res.status_code == 200
        assert Blog.objects.get(id=blog_id).title == 'Inverter care 101'

    def test_delete_post(self, admin_api_client, category):
        b = _blog(category)
        assert admin_api_client.delete(f'/blogs/admin/posts/{b.id}/').status_code == 204
        assert not Blog.objects.exists()

    def test_category_create_and_count(self, admin_api_client, category):
        _blog(category)
        res = admin_api_client.post('/blogs/admin/categories/', {'name': 'EV'}, format='json')
        assert res.status_code == 201
        counts = {c['name']: c['blog_count'] for c in admin_api_client.get('/blogs/admin/categories/').data}
        assert counts == {'Solar tips': 1, 'EV': 0}

    def test_category_delete_blocked_while_it_has_posts(self, admin_api_client, category):
        _blog(category)
        res = admin_api_client.delete(f'/blogs/admin/categories/{category.id}/')
        assert res.status_code == 400
        assert Blog.objects.exists()

    def test_empty_category_can_be_deleted(self, admin_api_client, category):
        assert admin_api_client.delete(f'/blogs/admin/categories/{category.id}/').status_code == 204


@pytest.mark.django_db
class TestCategoryNames:

    def test_duplicate_name_ignores_case_and_spaces(self, admin_api_client, category):
        res = admin_api_client.post('/blogs/admin/categories/', {'name': '  solar   TIPS '}, format='json')
        assert res.status_code == 400
        assert 'already exists' in str(res.data['name'])

    def test_rename_to_own_name_in_other_case_allowed(self, admin_api_client, category):
        res = admin_api_client.patch(f'/blogs/admin/categories/{category.id}/', {'name': 'Solar Tips'}, format='json')
        assert res.status_code == 200
        category.refresh_from_db()
        assert category.name == 'Solar Tips'

    def test_update_priority(self, admin_api_client, category):
        res = admin_api_client.patch(f'/blogs/admin/categories/{category.id}/', {'appreciation_mark': 7}, format='json')
        assert res.status_code == 200
        category.refresh_from_db()
        assert category.appreciation_mark == 7
