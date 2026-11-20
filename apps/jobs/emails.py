"""
"You've been assigned a job" emails (the sheet's assignment notification).

Sent to each person newly added to a job — not to people already on it, and
not to whoever made the change if they added themselves — when their staff
account has an email address. Sent after the save, by a Celery task (falls back
to sending straight away if the queue is down). Uses the shared dashboard email
theme (``apps.core.mail``); everything typed by staff is HTML-escaped. Failures
are logged, never raised.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils.html import escape, linebreaks

from apps.core.mail import ACCENT, button_html, send_html, wrap_html
from apps.staff.access import has_perm

from .models import Job, staff_name

logger = logging.getLogger(__name__)


def assignment_recipients(added, by):
    """The newly added people to email: active, with an email, and not the person assigning."""
    return [u for u in added if u.is_active and u.email and u.pk != getattr(by, 'pk', None)]


def queue_assignment_emails(job, added, by):
    """Email ``added`` once the current transaction commits."""
    recipients = [u.pk for u in assignment_recipients(added, by)]
    if not recipients:
        return
    by_id = getattr(by, 'pk', None)

    def go():
        from .tasks import send_job_assignment_emails
        try:
            send_job_assignment_emails.delay(job.pk, recipients, by_id)
        except Exception:  # noqa: BLE001 — queue down: send now rather than lose them
            logger.warning('Celery unavailable; sending job assignment emails straight away.', exc_info=True)
            send_job_assignment_emails(job.pk, recipients, by_id)

    transaction.on_commit(go)


def _row(label, value):
    return (f'<tr><td style="padding: 4px 12px 4px 0; color: #666; white-space: nowrap; vertical-align: top;">{label}</td>'
            f'<td style="padding: 4px 0;">{value}</td></tr>')


def send_assignment_email(job, user, by=None):
    """One person's email. Returns True when it went out."""
    if not (user.is_active and user.email):
        return False
    others = [staff_name(u) for u in job.assigned_to.all() if u.pk != user.pk]
    visit = ''
    if job.scheduled_date:
        visit = job.scheduled_date.strftime('%a %d %b %Y') + (f', {job.scheduled_time}' if job.scheduled_time else '')
    elif job.scheduled_time:
        visit = job.scheduled_time
    site = ''
    if job.site_id:
        site = job.site.name + (f' — {job.site.address}' if job.site.address else '')

    rows = [
        _row('Job', f'<strong>{escape(job.job_id)}</strong>'),
        _row('Category', escape(job.category.name)),
        _row('Client / job', escape(job.client_name or '—')),
        _row('Site', escape(site)) if site else '',
        _row('Status', escape(job.get_status_display())),
        _row('Visit', escape(visit)) if visit else '',
        _row('Also on this job', escape(', '.join(others))) if others else '',
    ]
    task = (f'<div style="margin: 12px 0; padding: 10px 12px; background: #f8f9fa; border-left: 3px solid {ACCENT};">'
            f'{linebreaks(escape(job.task))}</div>') if job.task else ''
    # The link only helps people who can open the Jobs page.
    link = (button_html(f'{settings.FRONTEND_BASE_URL}/dashboard/jobs/{job.pk}', 'Open the job')
            if has_perm(user, 'jobs.view') else '')
    who = f' by <strong>{escape(staff_name(by))}</strong>' if by else ''
    body = f"""
        <p style="margin: 0 0 12px;">Hi {escape(user.first_name or staff_name(user))},</p>
        <p style="margin: 0 0 12px;">You've been assigned to a job{who}.</p>
        <table style="border-collapse: collapse; font-size: 14px;">{''.join(rows)}</table>
        {task}
        {link}
    """
    subject = f'APS: You’ve been assigned {job.job_id} — {job.client_name or job.category.name}'
    return send_html(subject, wrap_html('New Job Assigned', body), [user.email], log_prefix='Job assignment')


def send_assignment_emails(job_id, user_ids, by_id=None):
    """Email each of ``user_ids`` about ``job_id``. Returns how many went out."""
    job = Job.objects.select_related('category', 'site').prefetch_related('assigned_to').filter(pk=job_id).first()
    if job is None:  # deleted for good before the task ran
        return 0
    by = User.objects.filter(pk=by_id).first() if by_id else None
    assigned = {u.pk for u in job.assigned_to.all()}
    sent = 0
    # Only people still on the job (they may have been taken off again before the task ran).
    for user in User.objects.filter(pk__in=[pk for pk in user_ids if pk in assigned]):
        if send_assignment_email(job, user, by):
            sent += 1
    logger.info(f'{job.job_id}: assignment email sent to {sent} of {len(user_ids)} person(s)')
    return sent


# ── Visits today (07:00, see tasks.send_visits_today) ─────────────────────────

def _visit_rows(jobs, links):
    """One table row per visit: job ID (a link when ``links``), client, task, time, assignees."""
    rows = []
    for job in jobs:
        jid = escape(job.job_id)
        if links:
            jid = f'<a href="{escape(f"{settings.FRONTEND_BASE_URL}/dashboard/jobs/{job.pk}")}" style="color: {ACCENT};">{jid}</a>'
        task = job.task.strip()
        task = task if len(task) <= 140 else task[:139].rstrip() + '…'
        people = ', '.join(staff_name(u) for u in job.assigned_to.all()) or 'Nobody yet'
        cell = 'padding: 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top;'
        sub = f'<br><span style="color: #666;">{escape(task)}</span>' if task else ''
        rows.append(
            f'<tr><td style="{cell} white-space: nowrap;"><strong>{jid}</strong></td>'
            f'<td style="{cell}">{escape(job.client_name or job.category.name)}{sub}</td>'
            f'<td style="{cell} white-space: nowrap;">{escape(job.scheduled_time.strip() or "Any time")}</td>'
            f'<td style="{cell}">{escape(people)}</td></tr>'
        )
    head = 'padding: 8px; text-align: left; border-bottom: 2px solid #d1d5db; font-size: 12px; color: #666;'
    return (f'<table style="width: 100%; border-collapse: collapse; font-size: 14px;">'
            f'<tr><th style="{head}">Job</th><th style="{head}">Client / task</th><th style="{head}">Time</th>'
            f'<th style="{head}">Assigned</th></tr>{"".join(rows)}</table>')


def visits_email_html(jobs, today, greeting, links):
    count = f'{len(jobs)} visit{"" if len(jobs) == 1 else "s"}'
    body = f"""
        <p style="margin: 0 0 12px;">{greeting}</p>
        <p style="margin: 0 0 12px;"><strong>{count}</strong> booked for today, {today:%A %d %B %Y}.</p>
        {_visit_rows(jobs, links)}
        {button_html(f'{settings.FRONTEND_BASE_URL}/dashboard/jobs?visit=today', 'Open Visits today') if links else ''}
    """
    return wrap_html('Visits Today', body)


def send_visits_today_emails(jobs, today):
    """One email with every visit to EMAIL_RECIPIENT, and each assigned staff
    member with an email gets their own list (links only if they can open
    Jobs). Nothing when there are no visits. Returns how many emails went out."""
    if not jobs:
        return 0
    subject_day = f'{today:%a %d %b}'
    sent = 0
    team = getattr(settings, 'EMAIL_RECIPIENT', '')
    if team and send_html(f'APS: Visits today ({subject_day}) — {len(jobs)} booked',
                          visits_email_html(jobs, today, 'Good morning,', links=True), [team],
                          log_prefix='Visits today'):
        sent += 1
    mine = {}
    for job in jobs:
        for user in job.assigned_to.all():
            if user.is_active and user.email:
                mine.setdefault(user.pk, (user, []))[1].append(job)
    for user, own in mine.values():
        html = visits_email_html(own, today, f'Good morning {escape(user.first_name or staff_name(user))},',
                                 links=has_perm(user, 'jobs.view'))
        if send_html(f'APS: Your visits today ({subject_day}) — {len(own)}', html, [user.email],
                     log_prefix='Visits today'):
            sent += 1
    logger.info(f'Visits today: {len(jobs)} visit(s), {sent} email(s) sent')
    return sent
