"""
Every trashable model in the project, found from ``TrashableModel`` subclasses,
plus the clean-up that deletes old trash for good.

A type's key is its model label (``'operations.job'``) — the same string the
audit history stores as ``object_type``.
"""

import logging
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.utils import timezone

from .models import TrashableModel

logger = logging.getLogger(__name__)


def retention_days():
    return int(getattr(settings, 'TRASH_RETENTION_DAYS', 90))


def trash_types():
    """{label: model} for every concrete ``TrashableModel``."""
    return {m._meta.label_lower: m for m in apps.get_models()
            if issubclass(m, TrashableModel) and not m._meta.abstract}


def type_label(model):
    return model.trash_label or str(model._meta.verbose_name).capitalize()


def purge_expired(now=None):
    """Delete for good everything trashed more than ``retention_days()`` ago.

    Returns {type: count}. Each item is removed on its own (with its audit
    entry), so one that can't be deleted doesn't stop the rest.
    """
    cutoff = (now or timezone.now()) - timedelta(days=retention_days())
    done = {}
    for key, model in trash_types().items():
        for obj in list(model.all_objects.filter(deleted_at__lt=cutoff)):
            try:
                obj.delete_forever()
            except Exception:
                logger.exception('Trash clean-up could not delete %s #%s', key, obj.pk)
                continue
            done[key] = done.get(key, 0) + 1
    return done
