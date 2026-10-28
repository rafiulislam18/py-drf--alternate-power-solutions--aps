"""
Site content and the trash: deleting a service, project, blog post or blog
category moves it to the trash — off the public website until it's restored.
A service takes its projects with it (and brings them back); a post or project
can't come back while its category / service is still in the trash.
"""

from datetime import date

import pytest
from django.contrib.auth.models import User

from apps.blog.models import Blog, BlogCategory
from apps.core.models import ClientProfile
from apps.records.models import AuditLog
from apps.services_and_projects.models import Project, Service

from ..client_portal.conftest import api_for

pytestmark = pytest.mark.django_db
RESTORE = '/records/trash/{}/{}/restore/'


@pytest.fixture
def boss(db):
    user = User.objects.create_user('boss', password='x', is_staff=True, is_superuser=True)
    ClientProfile.objects.create(user=user, role='admin')
    return user


def _service(title='Solar'):
    return Service.objects.create(title=title, short_description='s', long_description='l', image='services/x.jpg')


def _project(service, title='Roof install'):
    return Project.objects.create(service=service, title=title, short_description='s', long_description='l',
                                  image='projects/x.jpg', location='Cape Town', completion_date=date(2026, 5, 1),
                                  duration='2 weeks')


def _post(category, title='Load shedding tips'):
    return Blog.objects.create(category=category, title=title, short_description='s', long_description='l',
                               image='blogs/x.jpg', author='APS', date=date(2026, 9, 1), read_time='3 min')


def test_service_goes_to_the_trash_with_its_projects_and_comes_back_with_them(boss):
    api = api_for(boss)
    svc = _service()
    p1, p2, earlier = _project(svc, 'One'), _project(svc, 'Two'), _project(svc, 'Earlier')
    assert api.delete(f'/services-projects/admin/projects/{earlier.pk}/').status_code == 204

    assert api.delete(f'/services-projects/admin/services/{svc.pk}/').status_code == 204
    assert not Service.objects.exists() and not Project.objects.exists()
    assert api_for().get('/services-projects/').data == []                       # public site
    assert api_for().get(f'/services-projects/project/{p1.pk}').status_code == 404
    assert {i['name'] for i in api.get('/records/trash/').data['results']} == \
        {'Solar', 'One (Solar)', 'Two (Solar)', 'Earlier (Solar)'}

    # A project can't come back on its own while its service is in the trash.
    res = api.post(RESTORE.format('services_and_projects.project', p1.pk))
    assert res.status_code == 400 and 'Restore the service first' in res.data['detail']

    assert api.post(RESTORE.format('services_and_projects.service', svc.pk)).status_code == 200
    assert set(Project.objects.values_list('title', flat=True)) == {'One', 'Two'}  # not the earlier one
    assert api.get('/services-projects/admin/services/').data[0]['project_count'] == 2


def test_blog_post_trash_hides_it_from_the_site(boss):
    api = api_for(boss)
    cat = BlogCategory.objects.create(name='Tips')
    post = _post(cat)
    assert api.delete(f'/blogs/admin/posts/{post.pk}/').status_code == 204

    assert api_for().get(f'/blogs/{post.pk}').status_code == 404
    assert api_for().get('/blogs/').data[0]['blogs'] == []
    assert api.get('/blogs/admin/categories/').data[0]['blog_count'] == 0
    assert api.post(RESTORE.format('blog.blog', post.pk)).status_code == 200
    assert api_for().get(f'/blogs/{post.pk}').status_code == 200


def test_category_rules(boss):
    api = api_for(boss)
    cat = BlogCategory.objects.create(name='Tips')
    post = _post(cat)

    # Still has a live post → refused; once the post is in the trash it can go.
    assert api.delete(f'/blogs/admin/categories/{cat.pk}/').status_code == 400
    api.delete(f'/blogs/admin/posts/{post.pk}/')
    assert api.delete(f'/blogs/admin/categories/{cat.pk}/').status_code == 204
    assert api_for().get('/blogs/').data == []

    # Its name stays taken while it's in the trash (so it can be restored).
    res = api.post('/blogs/admin/categories/', {'name': 'tips'}, format='json')
    assert res.status_code == 400 and 'in the trash' in res.data['name'][0]

    res = api.post(RESTORE.format('blog.blog', post.pk))
    assert res.status_code == 400 and 'Restore the category first' in res.data['detail']
    assert api.post(RESTORE.format('blog.blogcategory', cat.pk)).status_code == 200
    assert api.post(RESTORE.format('blog.blog', post.pk)).status_code == 200


def test_content_changes_are_in_the_audit_history(boss):
    api = api_for(boss)
    res = api.post('/blogs/admin/categories/', {'name': 'News'}, format='json')
    api.patch(f"/blogs/admin/categories/{res.data['id']}/", {'name': 'EV news'}, format='json')
    api.delete(f"/blogs/admin/categories/{res.data['id']}/")

    entries = list(AuditLog.objects.filter(object_type='blog.blogcategory').order_by('id'))
    assert [e.action for e in entries] == ['created', 'updated', 'deleted']
    assert entries[1].changes == {'name': {'label': 'Name', 'old': 'News', 'new': 'EV news'}}
    assert all(e.section == 'blogs' and e.actor_role == 'staff' for e in entries)
