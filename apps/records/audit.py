"""
Writing audit history from a module's views.

The quick way, for DRF generic views / viewsets::

    class JobDetail(AuditedViewMixin, generics.RetrieveUpdateDestroyAPIView): ...

creates log "Created", edits log "Edited" with old → new values, and DELETE
moves a ``TrashableModel`` to the trash (logging "Moved to trash").

By hand, anywhere else::

    before = snapshot(job)
    ...change and save job...
    log_update(request.user, job, before)          # nothing logged if nothing changed
    log(request.user, 'assigned', job, summary='Assigned to Sediek')

A model can leave fields out of the history with ``audit_exclude = {'field'}``
(``auto_now`` timestamps, the trash fields and JSON blobs other than a plain
list of text are always left out).
"""

import datetime
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from .models import AuditLog, Target, display_name

ALWAYS_EXCLUDED = {'id', 'deleted_at', 'deleted_by', 'password', 'last_login'}
_SKIP = object()  # a value that can't be shown as text (a JSON blob)


def _fields(obj):
    skip = ALWAYS_EXCLUDED | set(getattr(obj, 'audit_exclude', ()))
    for field in obj._meta.concrete_fields:
        if field.primary_key or field.name in skip:
            continue
        if getattr(field, 'auto_now', False) or getattr(field, 'auto_now_add', False):
            continue
        yield field


def _display(obj, field):
    """The field's value as a person would read it (always a string)."""
    if field.is_relation:
        related = getattr(obj, field.name, None)
        return str(related) if related is not None else ''
    value = getattr(obj, field.attname)
    if field.choices:
        return str(dict(field.flatchoices).get(value, value) if value not in (None, '') else '')
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, datetime.datetime):
        return timezone.localtime(value).strftime('%d %b %Y %H:%M') if timezone.is_aware(value) else value.strftime('%d %b %Y %H:%M')
    if isinstance(value, datetime.date):
        return value.strftime('%d %b %Y')
    if isinstance(value, Decimal):
        return format(value.normalize(), 'f') if value == value.to_integral() else str(value)
    if isinstance(value, models.fields.files.FieldFile):
        return value.name or ''
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return '; '.join(value)
    if isinstance(value, (dict, list)):
        return _SKIP
    return str(value)


def snapshot(obj):
    """{field name: (label, value as text)} — take it before changing ``obj``."""
    shown = ((f, _display(obj, f)) for f in _fields(obj))
    return {f.name: (str(f.verbose_name).capitalize(), value) for f, value in shown if value is not _SKIP}


def diff(before, after):
    """{field: {label, old, new}} for the fields whose value changed."""
    return {name: {'label': label, 'old': before[name][1], 'new': value}
            for name, (label, value) in after.items()
            if name in before and before[name][1] != value}


def log(user, action, obj, changes=None, summary='', section=None):
    """Any action. ``obj`` is a model instance or a ``Target``."""
    return AuditLog.record(user, action, obj, changes=changes, summary=summary, section=section)


def account(user):
    """A dashboard login as the subject of an entry ("Sipho Dlamini (sipho)")."""
    name = display_name(user)
    return Target('auth.user', user.pk, name if name == user.username else f'{name} ({user.username})')


def change(label, old, new):
    """One {label, old, new} entry for ``changes`` built by hand."""
    return {'label': label, 'old': old, 'new': new}


def log_create(user, obj, summary=''):
    return AuditLog.record(user, AuditLog.CREATED, obj, summary=summary)


def log_remove(user, obj, summary=''):
    """Something deleted straight away (it doesn't go to the trash)."""
    return AuditLog.record(user, AuditLog.REMOVED, obj, summary=summary)


def log_update(user, obj, before, summary=''):
    """Log an edit if anything changed since ``before`` (from :func:`snapshot`)."""
    changes = diff(before, snapshot(obj))
    if not changes and not summary:
        return None
    return AuditLog.record(user, AuditLog.UPDATED, obj, changes=changes, summary=summary)


class AuditedViewMixin:
    """Audit + trash for DRF generic views (see the module docstring)."""

    @transaction.atomic
    def perform_create(self, serializer):
        obj = serializer.save()
        log_create(self.request.user, obj)

    @transaction.atomic
    def perform_update(self, serializer):
        before = snapshot(serializer.instance)
        obj = serializer.save()
        log_update(self.request.user, obj, before)

    @transaction.atomic
    def perform_destroy(self, instance):
        if hasattr(instance, 'trash'):
            instance.trash(self.request.user)
        else:
            log(self.request.user, AuditLog.REMOVED, instance)
            instance.delete()
