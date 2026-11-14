"""
Archive, shared by jobs, quote requests, solar estimates and tickets.

Archived = finished, kept forever, hidden from the everyday lists. It's not
the trash: nothing archived is ever deleted, and it can always be unarchived.
The model side is ``ArchivableModel`` (``apps.records.models``).

Lists (``filter_archived``):
    (default)            active items only
    ?archived=1          the archive view: archived items only
    ?include_archived=1  both (search "Include archived")

Auto rules (``apply_auto_archive``, called by a type's dashboard API views,
never by imports or management commands):
- An item is archived automatically only when a save takes it INTO its rule
  (``auto_archive_note`` was None before the save and isn't after). Saves
  that leave the rule's fields alone, comments and chat messages never archive.
- An auto-archived item that a save takes OUT of the rule comes back to active
  in that same save. One archived by hand stays archived.
- So an item unarchived while still inside its rule stays active until it
  leaves the rule and enters it again (e.g. Unpaid, then Paid again).

Archive and unarchive are logged as their own audit entries ("Archived",
"Unarchived"), never as field changes.
"""

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.staff.permissions import HasDashboardPerm

from .models import ArchivableModel, display_name

BULK_LIMIT = 200
_YES = ('1', 'true', 'yes')


def archive_view(params):
    """'archived', 'all' (include archived) or 'active' for a list request."""
    if (params.get('archived') or '').lower() in _YES:
        return 'archived'
    if (params.get('include_archived') or '').lower() in _YES:
        return 'all'
    return 'active'


def filter_archived(qs, params):
    view = archive_view(params)
    if view == 'archived':
        return qs.filter(archived_at__isnull=False)
    if view == 'active':
        return qs.filter(archived_at__isnull=True)
    return qs


def archived_count(qs):
    """How many archived items (for the "Archived (n)" switch)."""
    return qs.filter(archived_at__isnull=False).count()


def archive_data(obj, with_name=True):
    """The archive fields of an item for the API. Clients get no staff names
    (``with_name=False``)."""
    data = {
        'archived_at': obj.archived_at,
        'archive_reason': obj.archive_reason,
        'archive_note': obj.archive_note,
    }
    if with_name:
        by = obj.archived_by
        data['archived_by'] = {'id': by.pk, 'name': display_name(by)} if by else None
    return data


def meets_auto_rule(obj):
    return obj.auto_archive_note() is not None


def apply_auto_archive(obj, met_before, user):
    """Run the type's auto rule after a save. ``met_before`` is
    ``meets_auto_rule(obj)`` taken before the change. Returns the event for
    the response (``{action, automatic, note}``) or None."""
    note = obj.auto_archive_note()
    if note is not None and not met_before and not obj.is_archived:
        obj.archive(user, ArchivableModel.Reason.AUTO, note)
        return {'action': 'archived', 'automatic': True, 'note': note}
    if note is None and met_before and obj.is_archived and obj.archive_reason == ArchivableModel.Reason.AUTO:
        why = obj.auto_unarchive_note()
        obj.unarchive(user, summary=f'Unarchived automatically: {why}')
        return {'action': 'unarchived', 'automatic': True, 'note': why}
    return None


def set_archived(obj, archived, user):
    """Archive (by hand) or unarchive. False if it was already that way."""
    if obj.is_archived == archived:
        return False
    if archived:
        obj.archive(user)
    else:
        obj.unarchive(user)
    return True


class ArchiveView(APIView):
    """``POST <item>/archive/`` and ``<item>/unarchive/`` (``archived = False``).
    A subclass sets ``section`` (needs ``<section>.archive``) and ``get_item``."""

    permission_classes = [HasDashboardPerm]
    section = ''
    archived = True

    def dashboard_perm(self, request):
        return f'{self.section}.archive' if request.method == 'POST' else None

    def get_item(self, **kwargs):
        raise NotImplementedError

    def item_data(self, obj):
        return {'id': obj.pk, **archive_data(obj)}

    def after_change(self, obj):
        """Hook: the item was archived / unarchived (e.g. tell open pages)."""

    @transaction.atomic
    def post(self, request, **kwargs):
        obj = self.get_item(**kwargs)
        if set_archived(obj, self.archived, request.user):
            self.after_change(obj)
        return Response(self.item_data(obj))


class BulkArchiveView(APIView):
    """``POST bulk-archive/ {ids: [...], archived: true|false}`` →
    ``{done: [...], skipped: [...]}``; skipped = not found or already that way.
    A subclass sets ``section`` and ``find(ids)`` → {id: item}; ids are ints
    unless ``clean_id`` says otherwise (quotes use "general-12" keys)."""

    permission_classes = [HasDashboardPerm]
    section = ''

    def dashboard_perm(self, request):
        return f'{self.section}.archive' if request.method == 'POST' else None

    def find(self, ids):
        raise NotImplementedError

    def clean_id(self, value):
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def after_change(self, obj):
        """Hook: the item was archived / unarchived (e.g. tell open pages)."""

    @transaction.atomic
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        ids, archived = data.get('ids'), data.get('archived')
        if not isinstance(ids, list) or not isinstance(archived, bool):
            return Response({'detail': 'Send {"ids": [...], "archived": true or false}.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(ids) > BULK_LIMIT:
            return Response({'detail': f'Pick at most {BULK_LIMIT} items at a time.'},
                            status=status.HTTP_400_BAD_REQUEST)
        keys = list(dict.fromkeys(k for k in map(self.clean_id, ids) if k is not None))
        items = self.find(keys)
        done = [k for k in keys if k in items and set_archived(items[k], archived, request.user)]
        for key in done:
            self.after_change(items[key])
        return Response({'done': done, 'skipped': [k for k in keys if k not in done]})
