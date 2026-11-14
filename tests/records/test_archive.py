"""
Archive (apps.records.archive): the shared model fields, the auto rule's
transitions (including the "changed after archiving" edge cases), the list
filters, audit entries, and archive vs trash.
"""

import pytest
from django.contrib.auth.models import User

from apps.records import archive, audit
from apps.records.models import AuditLog

from .testapp.models import Gadget

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return User.objects.create_user('sam', first_name='Sam', last_name='Daniels')


def save_status(gadget, status, user):
    """What a dashboard save does: note the rule before, change, save, apply."""
    met_before = archive.meets_auto_rule(gadget)
    gadget.status = status
    gadget.save()
    return archive.apply_auto_archive(gadget, met_before, user)


def entries(gadget):
    return list(AuditLog.objects.filter(object_id=str(gadget.pk), object_type=Gadget._meta.label_lower)
                .order_by('id').values_list('action', 'summary'))


def test_archive_and_unarchive_by_hand(user):
    g = Gadget.objects.create()
    g.archive(user)
    g.refresh_from_db()
    assert g.is_archived and g.archived_by == user and g.archive_reason == 'manual' and g.archive_note == ''
    g.archive(user)  # already archived: nothing new
    g.unarchive(user)
    g.refresh_from_db()
    assert not g.is_archived and g.archived_by is None and g.archive_reason == ''
    assert entries(g) == [('archived', 'Archived'), ('unarchived', 'Unarchived')]


def test_entering_the_rule_archives_automatically(user):
    g = Gadget.objects.create()
    event = save_status(g, 'done', user)
    assert event == {'action': 'archived', 'automatic': True, 'note': 'done'}
    g.refresh_from_db()
    assert g.archive_reason == 'auto' and g.archive_note == 'done'
    assert entries(g)[-1] == ('archived', 'Archived automatically: done')


def test_saves_that_stay_inside_the_rule_do_nothing(user):
    g = Gadget.objects.create(status='done')  # created inside the rule outside the API (an import)
    assert save_status(g, 'done', user) is None
    assert not Gadget.objects.get(pk=g.pk).is_archived


def test_auto_archived_item_leaving_the_rule_comes_back(user):
    g = Gadget.objects.create()
    save_status(g, 'done', user)
    event = save_status(g, 'open', user)
    assert event == {'action': 'unarchived', 'automatic': True, 'note': 'status changed to open'}
    assert not Gadget.objects.get(pk=g.pk).is_archived
    assert entries(g)[-1] == ('unarchived', 'Unarchived automatically: status changed to open')


def test_manually_archived_item_leaving_the_rule_stays_archived(user):
    g = Gadget.objects.create(status='done')
    g.archive(user)
    assert save_status(g, 'open', user) is None
    assert Gadget.objects.get(pk=g.pk).archive_reason == 'manual'


def test_unarchived_item_inside_the_rule_is_not_archived_again_until_it_reenters(user):
    g = Gadget.objects.create()
    save_status(g, 'done', user)
    g.unarchive(user)  # Undo
    assert save_status(g, 'done', user) is None  # still done: stays active
    assert not Gadget.objects.get(pk=g.pk).is_archived
    save_status(g, 'open', user)
    assert save_status(g, 'done', user)['action'] == 'archived'  # a new completion


def test_entering_the_rule_while_archived_by_hand_keeps_the_manual_archive(user):
    g = Gadget.objects.create()
    g.archive(user)
    assert save_status(g, 'done', user) is None
    assert Gadget.objects.get(pk=g.pk).archive_reason == 'manual'


def test_list_filters(user):
    active = Gadget.objects.create()
    done = Gadget.objects.create()
    done.archive(user)
    qs = Gadget.objects.all()
    assert list(archive.filter_archived(qs, {})) == [active]
    assert list(archive.filter_archived(qs, {'archived': '1'})) == [done]
    assert set(archive.filter_archived(qs, {'include_archived': '1'})) == {active, done}
    assert archive.archived_count(qs) == 1


def test_archive_fields_stay_out_of_edit_diffs(user):
    g = Gadget.objects.create()
    before = audit.snapshot(g)
    assert not {'archived_at', 'archived_by', 'archive_reason', 'archive_note'} & set(before)
    g.archive(user)
    assert audit.log_update(user, g, before) is None


def test_archive_data_hides_the_name_from_clients(user):
    g = Gadget.objects.create()
    g.archive(user)
    assert archive.archive_data(g)['archived_by'] == {'id': user.pk, 'name': 'Sam Daniels'}
    assert 'archived_by' not in archive.archive_data(g, with_name=False)


def test_trash_and_restore_keep_the_archived_state(user):
    g = Gadget.objects.create(status='done')
    g.archive(user)
    g.trash(user)
    trashed = Gadget.all_objects.get(pk=g.pk)
    assert trashed.in_trash and trashed.is_archived
    trashed.restore(user)
    back = Gadget.objects.get(pk=g.pk)
    assert back.is_archived and back.archive_reason == 'manual' and back.archived_by == user
