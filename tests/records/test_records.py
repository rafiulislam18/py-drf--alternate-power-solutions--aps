"""
Trash and audit history (apps.records): deleting moves an item to the trash,
restoring brings it back, nobody deletes for good by hand — anything trashed
for over 90 days is deleted automatically — and the audit history can only
ever grow.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.core.models import ClientProfile
from apps.records import audit
from apps.records.models import AuditLog
from apps.records.tasks import purge_old_trash
from apps.staff import catalogue
from apps.staff.models import StaffAccess

from ..client_portal.conftest import PASSWORD, api_for, make_client
from .testapp.models import Widget, WidgetNote

pytestmark = pytest.mark.django_db
TRASH = '/records/trash/'
AUDIT = '/records/audit/'


def make_staff(username, perms=None):
    user = User.objects.create_user(username, first_name=username.capitalize(), password=PASSWORD)
    ClientProfile.objects.create(user=user, role='admin')
    if perms is not None:
        StaffAccess.objects.create(user=user, permissions=perms)
    return user


@pytest.fixture
def boss(db):
    user = User.objects.create_user('boss', password=PASSWORD, is_staff=True, is_superuser=True)
    ClientProfile.objects.create(user=user, role='admin')
    return user


@pytest.fixture
def thandi(db):
    """Staff with the default (read) permissions: reports.view + trash.view."""
    return make_staff('thandi', perms=catalogue.DEFAULT_PERMS)


# ── the model ────────────────────────────────────────────────────────────────

def test_trash_hides_and_restore_brings_back(thandi):
    w = Widget.objects.create(name='Inverter swap')
    w.trash(thandi)

    assert not Widget.objects.filter(pk=w.pk).exists()
    w = Widget.all_objects.get(pk=w.pk)
    assert w.in_trash and w.deleted_by == thandi

    w.restore(thandi)
    assert Widget.objects.filter(pk=w.pk).exists()
    w.refresh_from_db()
    assert w.deleted_at is None and w.deleted_by is None
    assert list(AuditLog.objects.filter(object_id=str(w.pk)).values_list('action', flat=True)) == \
        ['restored', 'deleted']


def test_children_stay_reachable_and_go_with_the_parent(boss):
    w = Widget.objects.create(name='COC Smith')
    note = WidgetNote.objects.create(widget=w, text='called')
    w.trash(boss)
    assert WidgetNote.objects.get(pk=note.pk).widget.name == 'COC Smith'  # trashed parent still reachable

    w.delete_forever()  # the automatic clean-up
    assert not Widget.all_objects.filter(pk=w.pk).exists()
    assert not WidgetNote.objects.filter(pk=note.pk).exists()
    entry = AuditLog.objects.first()
    assert (entry.action, entry.actor, entry.object_repr) == ('purged', None, 'COC Smith')


def test_audit_history_cannot_be_changed_or_deleted(boss):
    w = Widget.objects.create(name='x')
    entry = audit.log_create(boss, w)

    entry.summary = 'tampered'
    with pytest.raises(PermissionDenied):
        entry.save()
    with pytest.raises(PermissionDenied):
        entry.delete()
    with pytest.raises(PermissionDenied):
        AuditLog.objects.filter(pk=entry.pk).update(summary='tampered')
    with pytest.raises(PermissionDenied):
        AuditLog.objects.all().delete()
    assert AuditLog.objects.get(pk=entry.pk).summary == ''


def test_audit_survives_its_user_being_deleted(boss):
    temp = make_staff('temp')
    audit.log_create(temp, Widget.objects.create(name='x'))
    temp.delete()
    entry = AuditLog.objects.get()
    assert entry.actor_name == 'Temp'


def test_edits_log_old_and_new_values(boss, thandi):
    w = Widget.objects.create(name='Geyser', amount=Decimal('100.00'))
    before = audit.snapshot(w)
    w.status, w.amount, w.due, w.paid, w.owner, w.notes_text = 'done', Decimal('150.50'), date(2026, 11, 3), True, thandi, 'x'
    w.save()
    entry = audit.log_update(boss, w, before)

    assert entry.action == 'updated'
    assert entry.changes == {
        'status': {'label': 'Status', 'old': 'Open', 'new': 'Done'},
        'amount': {'label': 'Amount', 'old': '100', 'new': '150.50'},
        'due': {'label': 'Due', 'old': '', 'new': '03 Nov 2026'},
        'paid': {'label': 'Paid', 'old': 'No', 'new': 'Yes'},
        'owner': {'label': 'Owner', 'old': '', 'new': 'thandi'},
    }  # notes_text is excluded, updated_at (auto_now) always is

    assert audit.log_update(boss, w, audit.snapshot(w)) is None  # nothing changed → nothing logged


def test_old_trash_is_deleted_for_good_automatically(boss):
    old = Widget.objects.create(name='old')
    recent = Widget.objects.create(name='recent')
    live = Widget.objects.create(name='live')
    old.trash(boss)
    recent.trash(boss)
    Widget.all_objects.filter(pk=old.pk).update(deleted_at=timezone.now() - timedelta(days=91))
    Widget.all_objects.filter(pk=recent.pk).update(deleted_at=timezone.now() - timedelta(days=89))

    assert purge_old_trash() == {'records_testapp.widget': 1}
    assert set(Widget.all_objects.values_list('name', flat=True)) == {'recent', 'live'}
    entry = AuditLog.objects.filter(action='purged').get()
    assert entry.actor is None and entry.actor_name == '' and 'automatically' in entry.summary


# ── trash API ────────────────────────────────────────────────────────────────

def test_staff_see_only_what_they_deleted_superusers_see_all(boss, thandi):
    mine, theirs = Widget.objects.create(name='mine'), Widget.objects.create(name='theirs')
    mine.trash(thandi)
    theirs.trash(boss)

    res = api_for(thandi).get(TRASH)
    assert res.status_code == 200
    assert [i['name'] for i in res.data['results']] == ['mine']
    item = res.data['results'][0]
    assert item['type'] == 'records_testapp.widget' and item['type_label'] == 'Widget'
    assert item['deleted_by']['name'] == 'Thandi' and item['days_left'] == 89
    assert res.data['sees_everyone'] is False and res.data['retention_days'] == 90

    res = api_for(boss).get(TRASH, {'q': 'THE'})
    assert [i['name'] for i in res.data['results']] == ['theirs'] and res.data['sees_everyone'] is True


def test_trash_needs_the_permissions(boss):
    Widget.objects.create(name='x').trash(boss)
    no_trash = make_staff('notrash', perms=['reports.view'])
    no_section = make_staff('nosection', perms=['trash.view'])
    client = make_client('acme')

    assert api_for(no_trash).get(TRASH).status_code == 403
    assert api_for(no_section).get(TRASH).data['types'] == []
    assert api_for(client).get(TRASH).status_code == 403
    assert api_for().get(TRASH).status_code == 401


def test_staff_restore_their_own_items_only(boss, thandi):
    mine, theirs = Widget.objects.create(name='mine'), Widget.objects.create(name='theirs')
    mine.trash(thandi)
    theirs.trash(boss)
    api = api_for(thandi)

    assert api.post(f'{TRASH}records_testapp.widget/{theirs.pk}/restore/').status_code == 404
    res = api.post(f'{TRASH}records_testapp.widget/{mine.pk}/restore/')
    assert res.status_code == 200 and Widget.objects.filter(pk=mine.pk).exists()
    assert AuditLog.objects.filter(action='restored', actor=thandi, object_id=str(mine.pk)).exists()
    assert api.post(f'{TRASH}records_testapp.widget/{mine.pk}/restore/').status_code == 404  # not in trash now
    assert api.post(f'{TRASH}nope.model/{mine.pk}/restore/').status_code == 404


def test_superusers_restore_anyones_but_nobody_deletes_for_good(boss, thandi):
    w = Widget.objects.create(name='x')
    w.trash(thandi)
    for url in (f'{TRASH}records_testapp.widget/{w.pk}/', f'{TRASH}records_testapp.widget/{w.pk}/restore/'):
        assert api_for(boss).delete(url).status_code in (404, 405)
    assert Widget.all_objects.filter(pk=w.pk).exists()

    assert api_for(boss).post(f'{TRASH}records_testapp.widget/{w.pk}/restore/').status_code == 200
    assert AuditLog.objects.filter(action='restored', actor=boss).exists()


# ── audit API ────────────────────────────────────────────────────────────────

def test_full_audit_history_is_superuser_or_granted(boss, thandi):
    w = Widget.objects.create(name='Solar COC')
    audit.log_create(thandi, w)
    w.trash(boss)

    assert api_for(thandi).get(AUDIT).status_code == 403  # not a default permission
    granted = make_staff('auditor', perms=['audit.view_all'])
    assert api_for(granted).get(AUDIT).status_code == 200

    res = api_for(boss).get(AUDIT)
    assert res.status_code == 200
    assert [(e['action_label'], e['actor']['name']) for e in res.data['results']] == \
        [('Moved to trash', 'boss'), ('Created', 'Thandi')]
    assert res.data['filters']['actions'] == [{'key': 'created', 'label': 'Created'},
                                              {'key': 'deleted', 'label': 'Moved to trash'}]
    assert {a['name'] for a in res.data['filters']['actors']} == {'boss', 'Thandi'}

    assert len(api_for(boss).get(AUDIT, {'actor': thandi.pk}).data['results']) == 1
    assert len(api_for(boss).get(AUDIT, {'action': 'deleted'}).data['results']) == 1
    assert len(api_for(boss).get(AUDIT, {'q': 'solar'}).data['results']) == 2
    assert len(api_for(boss).get(AUDIT, {'type': 'records_testapp.widget'}).data['results']) == 2
    today = timezone.localdate().isoformat()
    assert len(api_for(boss).get(AUDIT, {'from': today, 'to': today}).data['results']) == 2
    assert len(api_for(boss).get(AUDIT, {'to': '2020-01-01'}).data['results']) == 0


def test_item_history_needs_the_section_view(boss, thandi):
    w = Widget.objects.create(name='x')
    audit.log_create(boss, w)
    url = f'/records/history/records_testapp.widget/{w.pk}/'

    res = api_for(thandi).get(url)
    assert res.status_code == 200 and [e['action'] for e in res.data] == ['created']
    assert api_for(make_staff('blind', perms=['trash.view'])).get(url).status_code == 403


# ── demo data command ────────────────────────────────────────────────────────

def test_demo_audit_data_is_local_only_and_removable(boss, thandi, settings):
    from django.core.management import CommandError, call_command
    real = audit.log_create(boss, Widget.objects.create(name='real'))

    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command('seed_audit_demo')
    assert AuditLog.objects.count() == 1

    settings.DEBUG = True
    call_command('seed_audit_demo')
    demo = AuditLog.objects.filter(object_type__startswith='demo.')
    assert demo.count() > 30 and {e.actor_name for e in demo} >= {'boss', 'Thandi', ''}
    res = api_for(boss).get(AUDIT, {'type': 'demo.tool_booking'})
    assert res.data['results'][0]['type_label'] == 'Tool booking'

    call_command('seed_audit_demo', '--clear')
    assert list(AuditLog.objects.all()) == [real]  # only the demo rows went


# ── trash preview ────────────────────────────────────────────────────────────

def test_trash_preview_shows_the_details_and_follows_the_same_rules(boss, thandi):
    from datetime import date as d
    mine = Widget.objects.create(name='Geyser COC', status='done', amount=Decimal('1500'), due=d(2026, 11, 3))
    theirs = Widget.objects.create(name='theirs')
    mine.trash(thandi)
    theirs.trash(boss)

    res = api_for(thandi).get(f'{TRASH}records_testapp.widget/{mine.pk}/')
    assert res.status_code == 200
    assert res.data['name'] == 'Geyser COC' and res.data['deleted_by']['name'] == 'Thandi'
    assert res.data['days_left'] == 89 and res.data['restore_blocked'] == ''
    fields = {f['label']: f['value'] for f in res.data['fields']}
    assert fields == {'Name': 'Geyser COC', 'Status': 'Done', 'Amount': '1500', 'Due': '03 Nov 2026', 'Paid': 'No'}

    # Same visibility as restoring: not someone else's (unless superuser), not a live item.
    assert api_for(thandi).get(f'{TRASH}records_testapp.widget/{theirs.pk}/').status_code == 404
    assert api_for(boss).get(f'{TRASH}records_testapp.widget/{mine.pk}/').status_code == 200
    live = Widget.objects.create(name='live')
    assert api_for(boss).get(f'{TRASH}records_testapp.widget/{live.pk}/').status_code == 404
    assert api_for(make_staff('notrash', perms=['reports.view'])).get(
        f'{TRASH}records_testapp.widget/{mine.pk}/').status_code == 403
