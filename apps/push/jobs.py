"""
Push for jobs (APS dashboard staff).

- Job assigned: the moment someone is added to a job's assignees (same moment
  as the assignment email), to each newly added person with ``jobs.view``, never
  the person assigning. "Job assigned: APS-123 · {client}".
- Visits today: the 07:00 run (``apps.jobs.tasks.send_visits_today``). Each
  assigned staff member gets their own visits; accounts with "Receive admin
  alerts" get one push with all of them instead (only that one). Nothing when
  there are no visits. Same visits as the tile (``apps.jobs.services.todays_visits``).

Each person's own switches apply (``prefs.py``). One tag per job / per day, so a
repeat replaces the last one instead of stacking.
"""

import logging

from django.contrib.auth import get_user_model

from .prefs import JOB_ASSIGNED, VISITS_TODAY, admin_alert_users, subscriptions_for
from .queue import after_commit
from .send import deliver
from .tickets import preview

logger = logging.getLogger('apps.push')

VISITS_LISTED = 3


def client_of(job):
    return job.client_name or job.category.name


def queue_job_assigned_push(job, added, by):
    from .tasks import push_job_assigned
    by_id = getattr(by, 'pk', None)
    user_ids = [u.pk for u in added if u.pk != by_id]
    if user_ids:
        after_commit(push_job_assigned, [job.pk, user_ids, by_id])


def send_job_assigned_push(job_id, user_ids, by_id=None):
    from apps.jobs.models import Job
    job = Job.objects.select_related('category').prefetch_related('assigned_to').filter(pk=job_id).first()
    if job is None:  # deleted or in the trash before the task ran
        return None
    # Only people still on the job (they may have been taken off again), never the assigner.
    still = {u.pk for u in job.assigned_to.all()} & set(user_ids) - {by_id}
    users = get_user_model().objects.filter(pk__in=still).select_related('client_profile', 'staff_access')
    subs = subscriptions_for(users, JOB_ASSIGNED)
    if not subs:
        return None
    visit = ''
    if job.scheduled_date:
        visit = f'Visit {job.scheduled_date:%a %d %b}' + (f', {job.scheduled_time}' if job.scheduled_time else '') + '. '
    payload = {
        'app': 'aps',
        'title': f'Job assigned: {job.job_id} · {client_of(job)}',
        'body': (visit + preview(job.task)).strip() or 'Open the job for the details.',
        'tag': f'aps-job-{job.pk}',
        'url': f'/dashboard/jobs/{job.pk}',
    }
    counts = deliver(subs, payload)
    logger.info(f'{job.job_id} assignment push: {counts}')
    return counts


def visit_line(job):
    time = job.scheduled_time.strip()
    return f'{job.job_id} {client_of(job)}' + (f' ({time})' if time else '')


def visits_payload(jobs, today):
    lines = [visit_line(j) for j in jobs[:VISITS_LISTED]]
    if len(jobs) > VISITS_LISTED:
        lines.append(f'+{len(jobs) - VISITS_LISTED} more')
    return {
        'app': 'aps',
        'title': f'Visits today: {len(jobs)}',
        'body': '\n'.join(lines),
        'tag': f'aps-visits-{today.isoformat()}',
        'url': '/dashboard/jobs?visit=today',
    }


def send_visits_today_push(jobs, today):
    """``jobs`` = today's visits in order. Returns {'people': n, 'sent': n}."""
    result = {'people': 0, 'sent': 0}
    if not jobs:
        return result
    admins = admin_alert_users(VISITS_TODAY)
    admin_ids = {u.pk for u in admins}
    sends = [(admins, jobs)]
    mine = {}
    for job in jobs:
        for user in job.assigned_to.all():
            if user.is_active and user.pk not in admin_ids:
                mine.setdefault(user.pk, (user, []))[1].append(job)
    sends += [([user], own) for user, own in mine.values()]
    for users, own in sends:
        subs = subscriptions_for(users, VISITS_TODAY)
        if subs:
            result['people'] += len({s.user_id for s in subs})
            result['sent'] += deliver(subs, visits_payload(own, today))['sent']
    logger.info(f'Visits today push ({len(jobs)} visit(s)): {result}')
    return result
