"""
Helpers for the dashboard "Site content" editor's uploaded images: a size cap,
and removing a file from storage once nothing points at it any more (a
replaced or cleared image, or a deleted service/project/blog post).
"""

from django.db import transaction
from rest_framework import serializers

MAX_CONTENT_IMAGE_BYTES = 10 * 1024 * 1024


def validate_image_size(value):
    if value and getattr(value, 'size', 0) > MAX_CONTENT_IMAGE_BYTES:
        raise serializers.ValidationError('Images must be 10 MB or smaller.')
    return value


def file_names(instance, fields):
    """Stored file names currently set on ``fields`` of ``instance``."""
    return {getattr(instance, f).name for f in fields if getattr(instance, f)}


def delete_unused_files(model, fields, names):
    """After commit, delete each stored file in ``names`` that no row of
    ``model`` references any more (so shared/seeded paths are never lost)."""
    names = {n for n in names if n}
    if not names:
        return

    def _cleanup():
        storage = model._meta.get_field(fields[0]).storage
        for name in names:
            if not any(model.objects.filter(**{f: name}).exists() for f in fields):
                storage.delete(name)

    transaction.on_commit(_cleanup)
