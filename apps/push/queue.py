"""Queue a push task once the current transaction commits (like the job emails)."""

import logging

from django.db import transaction

logger = logging.getLogger('apps.push')


def after_commit(task, args, countdown=None):
    def go():
        try:
            task.apply_async(args=args, countdown=countdown)
        except Exception:  # noqa: BLE001 — queue down: send now rather than lose it
            logger.warning(f'Celery unavailable; running {task.name} straight away.', exc_info=True)
            try:
                task(*args)
            except Exception:  # noqa: BLE001 — a notification must never break the save
                logger.exception(f'{task.name} failed')

    transaction.on_commit(go)
