"""
Trash and audit history, shared by every dashboard module.

- ``TrashableModel`` — an abstract base. Deleting an item puts it in the trash
  (``deleted_at`` + ``deleted_by``); restoring clears both. ``objects`` only
  ever returns live rows, so no list shows trashed items by accident;
  ``all_objects`` sees everything. Nobody deletes anything for good by hand:
  only the daily clean-up (``apps.records.tasks``) does, once an item has been
  in the trash for ``TRASH_RETENTION_DAYS``.
- ``AuditLog`` — who did what, when, to which item, with old → new values,
  for staff and client actions alike (``actor_role``). Rows can only ever be
  added: editing or deleting one raises, here and (on PostgreSQL) in the
  database itself (migration 0002).

The rules (who sees and restores what) are in docs/trash-and-audit-history.md.

Children of a trashed item (e.g. a job's comments) aren't trashed separately:
they're only ever reached through their parent, and deleting the parent for
good removes them through the normal CASCADE.
"""

from collections import namedtuple

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.utils import timezone

from apps.core.content_files import delete_unused_files, file_names

# What an audit entry is about when there's no single model row to point at
# (e.g. a cancelled subscription found by email): ``Target('subscription', ref, 'Name')``.
Target = namedtuple('Target', 'object_type object_id object_repr')


class TrashQuerySet(models.QuerySet):
    def live(self):
        return self.filter(deleted_at__isnull=True)

    def trashed(self):
        return self.filter(deleted_at__isnull=False)


class LiveManager(models.Manager):
    """Live (not trashed) rows only."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class RestoreBlocked(Exception):
    """An item can't come back yet (e.g. a newer item now takes its place)."""


class TrashableModel(models.Model):
    """Base for anything the dashboard can put in the trash.

    A subclass sets ``trash_section`` to its staff permission section (e.g.
    ``'jobs'``): staff need ``<section>.view`` to see it in the trash and its
    history. ``trash_label`` names the type on the Trash page ("Job").
    ``str(obj)`` is the item's name in the trash and the audit history.
    A subclass that has uniqueness rules or a trashable parent overrides
    ``restore_blocked_reason``. ``trash_preview(request)`` (optional) returns
    the data for the type's own Trash preview design — ``{'kind': '<type>', …}``;
    without it the preview lists the fields as text. ``trash_file_fields`` names its uploaded-file
    fields: the files stay while the item is in the trash and are removed
    (when nothing else uses them) once it's deleted for good.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   editable=False, related_name='+')

    objects = LiveManager()
    all_objects = TrashQuerySet.as_manager()

    trash_section = ''
    trash_label = ''
    trash_file_fields = ()

    class Meta:
        abstract = True

    @property
    def in_trash(self):
        return self.deleted_at is not None

    @transaction.atomic
    def trash(self, user, at=None, summary=''):
        """Move to the trash. ``at`` stamps several items as trashed together
        (a parent and its children), so they can be restored together."""
        if self.in_trash:
            return
        self.deleted_at = at or timezone.now()
        self.deleted_by = user if getattr(user, 'is_authenticated', False) else None
        self.save(update_fields=['deleted_at', 'deleted_by'])
        AuditLog.record(user, AuditLog.DELETED, self, summary=summary)

    def restore_blocked_reason(self):
        """A sentence saying why this item can't be restored right now, or ''."""
        return ''

    @transaction.atomic
    def restore(self, user, summary=''):
        """Put the item back. Raises ``RestoreBlocked`` if ``restore_blocked_reason`` says no."""
        if not self.in_trash:
            return
        reason = self.restore_blocked_reason()
        if reason:
            raise RestoreBlocked(reason)
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=['deleted_at', 'deleted_by'])
        AuditLog.record(user, AuditLog.RESTORED, self, summary=summary)

    def stored_files(self):
        """[(model, fields, file names)] to tidy up after deleting for good."""
        fields = list(self.trash_file_fields)
        return [(type(self), fields, file_names(self, fields))] if fields else []

    @transaction.atomic
    def delete_forever(self):
        """Remove the row for good. Only the automatic clean-up calls this — there
        is deliberately no way for a person to do it (see the docs)."""
        days = int(getattr(settings, 'TRASH_RETENTION_DAYS', 90))
        AuditLog.record(None, AuditLog.PURGED, self,
                        summary=f'Deleted for good automatically after {days} days in the trash')
        files = self.stored_files()
        self.delete()
        for model, fields, names in files:
            delete_unused_files(model, fields, names)


class _AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionDenied('Audit history can’t be changed.')

    def delete(self):
        raise PermissionDenied('Audit history can’t be deleted.')


class AuditLog(models.Model):
    CREATED, UPDATED, DELETED, RESTORED, PURGED = 'created', 'updated', 'deleted', 'restored', 'purged'
    # Deleted straight away: things that don't go to the trash (a ticket, a price-list row).
    REMOVED = 'removed'
    ACTIONS = [
        (CREATED, 'Created'),
        (UPDATED, 'Edited'),
        (DELETED, 'Moved to trash'),
        (RESTORED, 'Restored'),
        (PURGED, 'Deleted for good'),
        (REMOVED, 'Deleted'),
        ('signed_in', 'Signed in'),
        ('signed_up', 'Signed up'),
        ('email_confirmed', 'Email confirmed'),
        ('password_changed', 'Password changed'),
        ('password_reset', 'Password reset'),
        ('message_sent', 'Message sent'),
        ('confirmed', 'Confirmed'),
        ('reopened', 'Reopened'),
        ('cancelled', 'Cancelled'),
        ('marked_job', 'Marked as a job'),
        ('unmarked_job', 'Unmarked as a job'),
        ('dismissed', 'Marked not a job'),
        ('undismissed', 'Un-dismissed'),
        ('exported', 'Exported'),
    ]

    # Which side of the dashboard did it: the two Audit history pages.
    STAFF, CLIENT = 'staff', 'client'
    ROLES = [(STAFF, 'Staff & admin'), (CLIENT, 'Client')]

    created_at = models.DateTimeField(default=timezone.now, db_index=True, editable=False)
    # No database FK: an audit row must never be touched when its user goes
    # (an ON DELETE SET NULL would be an UPDATE, which the log refuses).
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.DO_NOTHING, db_constraint=False,
                              null=True, blank=True, related_name='+')
    actor_name = models.CharField(max_length=200, blank=True)  # as it was then; '' = the system
    # 'staff' / 'client' at the time; '' = the system (shown with staff & admin).
    actor_role = models.CharField(max_length=10, blank=True, choices=ROLES, db_index=True)
    # Free text, not a fixed choice list: modules add their own (e.g. 'assigned').
    action = models.CharField(max_length=30, db_index=True)
    object_type = models.CharField(max_length=100, db_index=True)  # model label, e.g. 'operations.job'
    object_id = models.CharField(max_length=64, db_index=True)
    object_repr = models.CharField(max_length=300)
    section = models.CharField(max_length=40, blank=True, db_index=True)  # staff permission section
    # {"field": {"label": "Status", "old": "OPEN", "new": "DONE"}}
    changes = models.JSONField(default=dict, blank=True)
    summary = models.CharField(max_length=500, blank=True)

    objects = models.Manager.from_queryset(_AppendOnlyQuerySet)()

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['object_type', 'object_id', '-created_at'])]
        verbose_name = 'audit entry'
        verbose_name_plural = 'audit history'

    def __str__(self):
        return f'{self.actor_name or "System"} · {self.action} · {self.object_repr}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionDenied('Audit history can’t be changed.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied('Audit history can’t be deleted.')

    @classmethod
    def record(cls, user, action, obj, changes=None, summary='', section=None):
        """Add one entry. ``user`` None / anonymous = done by the system.

        ``obj`` is a model instance or a ``Target``. ``section`` (the staff
        permission section) defaults to the model's ``trash_section`` /
        ``audit_section``.
        """
        actor = user if getattr(user, 'is_authenticated', False) else None
        if isinstance(obj, Target):
            object_type, object_id, object_repr = obj
        else:
            object_type, object_id, object_repr = obj._meta.label_lower, obj.pk, obj
        if section is None:
            section = getattr(obj, 'trash_section', '') or getattr(obj, 'audit_section', '') or ''
        return cls.objects.create(
            actor=actor,
            actor_name=display_name(actor) if actor else '',
            actor_role=role_of(actor),
            action=action,
            object_type=object_type,
            object_id=str(object_id),
            object_repr=str(object_repr)[:300],
            section=section,
            changes=changes or {},
            summary=summary[:500],
        )


def display_name(user):
    return (user.get_full_name() or user.username)[:200]


def role_of(user):
    """'staff' / 'client' for a dashboard user, '' for the system."""
    if user is None:
        return ''
    from apps.core.roles import get_role
    return AuditLog.STAFF if get_role(user) == 'admin' else AuditLog.CLIENT
