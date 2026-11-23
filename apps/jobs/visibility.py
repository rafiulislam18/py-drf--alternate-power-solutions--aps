"""
Who can see which job: one rule, used everywhere jobs are read.

- Staff with ``jobs.view`` ("View jobs — see all jobs") and superusers see
  every job.
- Any other staff member can still open the Jobs page, but only sees the jobs
  assigned to them or created by them.
- Clients never see jobs.

Every other job permission (edit, assign, delete, archive, comment, settings)
applies on top, and only to jobs the person can see. A job someone can't see
answers 404 everywhere, exactly like one that doesn't exist.
"""

from django.db.models import Q

from apps.core.roles import is_dashboard_admin
from apps.staff.access import has_perm

from .models import Job

SEE_ALL = 'jobs.view'


def sees_all_jobs(user):
    return is_dashboard_admin(user) and has_perm(user, SEE_ALL)


def visible_jobs(user, qs=None):
    """``qs`` (default: live jobs) narrowed to what ``user`` may see."""
    qs = Job.objects.all() if qs is None else qs
    if not is_dashboard_admin(user):
        return qs.none()
    if has_perm(user, SEE_ALL):
        return qs
    # A subquery for the assignees, so rows aren't repeated (no DISTINCT needed).
    mine = Job.assigned_to.through.objects.filter(user_id=user.pk).values('job_id')
    return qs.filter(Q(created_by_id=user.pk) | Q(pk__in=mine))


def can_see_job(user, job):
    if job is None or not is_dashboard_admin(user):
        return False
    if has_perm(user, SEE_ALL):
        return True
    return job.created_by_id == user.pk or job.assigned_to.filter(pk=user.pk).exists()


def is_on_job(user, job):
    """Assigned to it, or created it."""
    return job.created_by_id == user.pk or job.assigned_to.filter(pk=user.pk).exists()


def can_comment(user, job):
    """Comment on a job they can see: with "Comment on jobs", or anyone assigned to
    it or who created it (even without that permission)."""
    return can_see_job(user, job) and (has_perm(user, 'jobs.comment') or is_on_job(user, job))
