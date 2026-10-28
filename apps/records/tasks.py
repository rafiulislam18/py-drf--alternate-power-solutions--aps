import logging

from celery import shared_task

from .trash import purge_expired, retention_days

logger = logging.getLogger(__name__)


@shared_task
def purge_old_trash():
    """Daily (03:00 SAST, migration 0003): delete trash older than the retention period for good."""
    done = purge_expired()
    total = sum(done.values())
    if total:
        logger.info('Trash clean-up: deleted %s item(s) older than %s days for good: %s', total, retention_days(), done)
    return done
