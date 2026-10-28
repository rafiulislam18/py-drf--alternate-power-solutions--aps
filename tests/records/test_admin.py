"""
Trash and audit history in the Django admin: the read-only audit history, and
the TrashableAdmin base every trashable model's admin uses (trash filter,
Move to trash / Restore / Delete for good actions, saves logged).
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from apps.records import audit
from apps.records.models import AuditLog

from .testapp.models import Widget

pytestmark = pytest.mark.django_db
WIDGETS = '/admin/records_testapp/widget/'


@pytest.fixture
def boss(db):
    return User.objects.create_superuser('boss', 'boss@aps.example', 'pw')


def login(user):
    c = Client()
    c.force_login(user)
    return c


def test_audit_history_admin_is_read_only(boss):
    c = login(boss)
    w = Widget.objects.create(name='x', status='open')
    before = audit.snapshot(w)
    w.status = 'done'
    w.save()
    entry = audit.log_update(boss, w, before)

    assert c.get('/admin/records/auditlog/').status_code == 200
    res = c.get(f'/admin/records/auditlog/{entry.pk}/change/')
    assert res.status_code == 200 and b'Open' in res.content and b'Done' in res.content
    assert c.get('/admin/records/auditlog/add/').status_code == 403
    assert c.post(f'/admin/records/auditlog/{entry.pk}/delete/', {'post': 'yes'}).status_code == 403
    assert AuditLog.objects.filter(pk=entry.pk).exists()


def test_trash_filter_and_actions(boss):
    c = login(boss)
    live, gone = Widget.objects.create(name='live one'), Widget.objects.create(name='gone one')
    gone.trash(boss)

    res = c.get(WIDGETS)
    assert b'live one' in res.content and b'gone one' not in res.content  # default: not in trash
    res = c.get(WIDGETS, {'trash': 'trashed'})
    assert b'gone one' in res.content and b'live one' not in res.content

    c.post(WIDGETS, {'action': 'move_to_trash', '_selected_action': [live.pk]})
    assert not Widget.objects.filter(pk=live.pk).exists()
    c.post(f'{WIDGETS}?trash=all', {'action': 'restore_from_trash', '_selected_action': [live.pk]})
    assert Widget.objects.filter(pk=live.pk).exists()

    # There's no "delete for good" — not even for superusers.
    res = c.post(f'{WIDGETS}?trash=all', {'action': 'delete_for_good', '_selected_action': [gone.pk]})
    assert Widget.all_objects.filter(pk=gone.pk).exists()
    assert list(AuditLog.objects.values_list('action', flat=True)) == ['restored', 'deleted', 'deleted']


def test_staff_restore_in_the_admin_too(db):
    staff = User.objects.create_user('ops', password='pw', is_staff=True)
    from django.contrib.auth.models import Permission
    staff.user_permissions.add(*Permission.objects.filter(content_type__app_label='records_testapp'))
    w = Widget.objects.create(name='x')
    w.trash(staff)

    c = login(staff)
    res = c.get(f'{WIDGETS}?trash=trashed')
    assert b'delete_for_good' not in res.content and b'restore_from_trash' in res.content
    c.post(f'{WIDGETS}?trash=trashed', {'action': 'restore_from_trash', '_selected_action': [w.pk]})
    assert Widget.objects.filter(pk=w.pk).exists()


def test_saving_in_the_admin_is_audited(boss):
    c = login(boss)
    w = Widget.objects.create(name='Geyser')
    c.post(f'{WIDGETS}{w.pk}/change/', {'name': 'Geyser', 'status': 'done', 'amount': '0'})
    entry = AuditLog.objects.filter(action='updated').get()
    assert entry.actor == boss
    assert entry.changes['status'] == {'label': 'Status', 'old': 'Open', 'new': 'Done'}
