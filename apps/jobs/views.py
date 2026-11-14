"""
Operations jobs API (mounted at ``/jobs/``) — staff only, per permission
(``apps.staff.catalogue`` section ``jobs``).

    GET    /jobs/                  list: ?status &category &payment &assigned=<id>|me|none
                                   &attention=1 &awaiting_payment=1 &unassigned=1 &visit=today
                                   &q &ordering=default|stale|unpaid|newest|number &page
                                   &archived=1 (the archive) | &include_archived=1 (both)
                                   (+ ``summary``: those four counts over ALL active jobs -
                                   archived ones in the archive view - ignoring the filters,
                                   for the summary tiles; + ``archived_count``)
    POST   /jobs/                  create (jobs.create; + jobs.assign to assign people)
    GET    /jobs/<id>/             one job with its comments
    PATCH  /jobs/<id>/             edit (jobs.edit; jobs.assign for ``assigned_to``)
    DELETE /jobs/<id>/             move to the trash (jobs.delete)
    POST   /jobs/<id>/archive/     archive / unarchive (jobs.archive)
    POST   /jobs/<id>/unarchive/
    POST   /jobs/bulk-archive/     {ids, archived: true|false} (jobs.archive)
    POST   /jobs/<id>/comments/    add a comment (jobs.comment)
    PATCH  /jobs/<id>/comments/<c>/ edit your own comment (jobs.comment)
    GET    /jobs/options/          categories, statuses, staff, clients + sites, colour thresholds
    GET    /jobs/settings/         colour thresholds; PATCH needs jobs.settings
    POST   /jobs/categories/       add a category (jobs.settings)
    PATCH  /jobs/categories/<id>/  rename / reorder / switch off (jobs.settings) - never deleted

Any change to a job (field, assigned staff, comment) moves its "last updated"
and is written to the audit history. People newly assigned get an email
(apps.jobs.emails). A create or edit that makes a job done / invoiced + Paid
archives it (``archive_event`` in the response); see apps.records.archive.
"""

import logging
from datetime import datetime, time, timedelta
from functools import partial

from django.db import transaction
from django.db.models import Case, Count, DateField, F, IntegerField, Max, Prefetch, Q, Value, When
from django.db.models.functions import Coalesce, TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.records import archive, audit
from apps.records.models import AuditLog
from apps.staff.permissions import HasDashboardPerm
from apps.solar_dashboard.models import Site
from utils.exceptions import custom_exception_handler

from .emails import queue_assignment_emails
from .models import Job, JobCategory, JobComment, JobSettings, format_job_id, staff_name
from .services import next_number, save_with_next_number
from .serializers import (
    CommentSerializer, JobCategorySerializer, JobSettingsSerializer, JobWriteSerializer, client_label, client_queryset, comment_data,
    job_detail, job_row, staff_queryset,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
STATUS_RANK = Case(*[When(status=s, then=Value(i)) for i, s in enumerate(Job.Status.values)],
                   default=Value(len(Job.Status.values)), output_field=IntegerField())
NEWEST = [F('date_added').desc(nulls_last=True), '-last_updated', '-number']
# Done / invoiced but not marked Paid or No charge (unpaid, paid deposit or not set) — waiting since the
# completion date (or the last update when there's none), like the sheet's unpaid alert.
UNPAID = Q(status__in=Job.FINISHED) & ~Q(payment__in=Job.SETTLED)
UNPAID_SINCE = Case(When(UNPAID, then=Coalesce('completion_date', TruncDate('last_updated'))),
                    default=None, output_field=DateField())
# Live work nobody has been given yet.
UNASSIGNED = Q(assigned_to__isnull=True, status__in=Job.LIVE)
ORDERINGS = {
    'default': [STATUS_RANK, *NEWEST],     # the sheet's daily sort (no Date Added -> by Last Updated)
    'stale': ['last_updated', 'number'],   # longest without an update first
    # Not paid yet first, longest waiting first; then everything else in the default order.
    'unpaid': [UNPAID_SINCE.asc(nulls_last=True), STATUS_RANK, *NEWEST],
    'newest': NEWEST,
    'number': ['-number'],
}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _jobs():
    return Job.objects.select_related(
        'category', 'client__client_profile', 'site', 'created_by', 'archived_by',
    ).prefetch_related(
        Prefetch('assigned_to', queryset=staff_queryset().order_by('first_name', 'username')),
        Prefetch('comments', queryset=JobComment.objects.order_by('created_at', 'id')),
    )


def watch_cutoff(thresholds):
    """Jobs last updated before this are ``watch_days`` or more days old (by local date)."""
    day = timezone.localdate() - timedelta(days=thresholds.watch_days - 1)
    return timezone.make_aware(datetime.combine(day, time.min))


def needs_attention(thresholds):
    """Live jobs at "watch" or worse."""
    return Q(status__in=Job.LIVE, last_updated__lt=watch_cutoff(thresholds))


def visiting_today():
    """Booked for today (local date). Cancelled and not-accepted jobs aren't
    visited, so they don't count."""
    return Q(scheduled_date=timezone.localdate()) & ~Q(status__in=(Job.Status.CANCELLED, Job.Status.NOT_ACCEPTED))


def jobs_summary(thresholds, archived=False):
    """The summary tiles: counts over every active job (not in the trash) -
    or every archived one in the archive view - whatever the list is filtered
    by. Each matches its list filter. "Today" is the local date (TIME_ZONE,
    Africa/Johannesburg). Distinct counts, as the unassigned test joins the
    assignees."""
    def n(q):
        return Count('id', filter=q, distinct=True)
    return Job.objects.filter(archived_at__isnull=not archived).aggregate(
        attention=n(needs_attention(thresholds)),
        awaiting_payment=n(UNPAID),
        visits_today=n(visiting_today()),
        unassigned=n(UNASSIGNED),
    )


def _names(users):
    return '; '.join(sorted(staff_name(u) for u in users))


def apply_status_rules(job, old_status, completion_given):
    """The sheet's status rules: finishing stamps the completion date (if empty),
    reopening clears it and the unpaid-alert record."""
    if job.status == old_status:
        return
    if job.status in Job.FINISHED:
        if not job.completion_date:
            job.completion_date = timezone.localdate()
    else:
        if not completion_given:
            job.completion_date = None
        job.unpaid_alert_at = None


class _JobView(APIView):
    permission_classes = [HasDashboardPerm]

    def get_exception_handler(self):
        return partial(custom_exception_handler, keep_fields=True)


class JobListView(_JobView):
    def dashboard_perm(self, request):
        if request.method == 'POST':
            return ['jobs.create', 'jobs.assign'] if request.data.get('assigned_to') else 'jobs.create'
        return 'jobs.view'

    def get(self, request):
        p = request.query_params
        qs = archive.filter_archived(_jobs(), p)
        if _int(p.get('category')) is not None:
            qs = qs.filter(category_id=_int(p['category']))
        if p.get('payment') in Job.Payment.values:
            qs = qs.filter(payment=p['payment'])
        elif p.get('payment') == 'none':
            qs = qs.filter(payment='')
        assigned = p.get('assigned')
        if assigned == 'me':
            qs = qs.filter(assigned_to=request.user)
        elif assigned == 'none':
            qs = qs.filter(assigned_to__isnull=True)
        elif _int(assigned) is not None:
            qs = qs.filter(assigned_to__id=_int(assigned))
        q = ' '.join((p.get('q') or '').split())
        if q:
            match = Q(client_name__icontains=q) | Q(task__icontains=q) | Q(invoice__icontains=q) \
                | Q(comments__body__icontains=q)
            digits = q.upper().removeprefix('APS-').removeprefix('APS')
            if digits.isdigit():
                match |= Q(number=int(digits))
            qs = qs.filter(match).distinct()

        thresholds = JobSettings.load()
        if p.get('attention'):  # live jobs at "watch" or worse
            qs = qs.filter(needs_attention(thresholds))
        if p.get('awaiting_payment'):  # done / invoiced, not paid or no charge
            qs = qs.filter(UNPAID)
        if p.get('unassigned'):  # live, nobody assigned
            qs = qs.filter(UNASSIGNED).distinct()
        if p.get('visit') == 'today':  # booked for today (local date), not cancelled / not accepted
            qs = qs.filter(visiting_today())

        # Counts per status for the filter, before the status filter itself.
        found = dict(qs.order_by().values_list('status').annotate(n=Count('id', distinct=True)))
        counts = {s: found.get(s, 0) for s in Job.Status.values}
        counts['none'] = found.get('', 0)  # no status yet
        counts['all'] = sum(counts.values())

        statuses = [s if s != 'none' else '' for s in (p.get('status') or '').split(',')
                    if s in Job.Status.values or s == 'none']
        if statuses:
            qs = qs.filter(status__in=statuses)

        qs = qs.order_by(*ORDERINGS.get(p.get('ordering') or 'default', ORDERINGS['default']))
        total = qs.count()
        pages = max(1, -(-total // PAGE_SIZE))
        page = min(max(1, _int(p.get('page')) or 1), pages)
        today = timezone.localdate()
        rows = [job_row(j, thresholds, today) for j in qs[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]]
        return Response({'results': rows, 'count': total, 'page': page, 'total_pages': pages, 'counts': counts,
                         'summary': jobs_summary(thresholds, archive.archive_view(p) == 'archived'),
                         'archived_count': archive.archived_count(Job.objects.all())})

    def post(self, request):
        serializer = JobWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        assignees = data.pop('assigned_to', [])
        comment = (data.pop('comment', '') or '').strip()

        job = Job(**data, created_by=request.user)
        apply_status_rules(job, Job.Status.OPEN, 'completion_date' in data)
        save_with_next_number(job)
        with transaction.atomic():
            job.assigned_to.set(assignees)
            audit.log_create(request.user, job, summary=f'Assigned to {_names(assignees)}' if assignees else '')
            queue_assignment_emails(job, assignees, request.user)
            if comment:
                c = JobComment.objects.create(job=job, author=request.user, author_name=staff_name(request.user),
                                              body=comment)
                audit.log(request.user, 'commented', job, summary=c.body)
            event = archive.apply_auto_archive(job, False, request.user)
        logger.info(f'{request.user.username} created {job.job_id}')
        data = job_detail(_jobs().get(pk=job.pk), JobSettings.load(), request.user)
        return Response({**data, 'archive_event': event}, status=status.HTTP_201_CREATED)


class JobDetailView(_JobView):
    def dashboard_perm(self, request):
        if request.method == 'GET':
            return 'jobs.view'
        if request.method == 'DELETE':
            return 'jobs.delete'
        if request.method == 'PATCH':
            data = request.data if hasattr(request.data, 'keys') else {}
            codes = ['jobs.assign'] if 'assigned_to' in data else []
            if set(data) - {'assigned_to'} or not codes:
                codes.append('jobs.edit')
            return codes
        return None  # plain "method not allowed"

    def get(self, request, pk):
        job = get_object_or_404(_jobs(), pk=pk)
        return Response(job_detail(job, JobSettings.load(), request.user))

    @transaction.atomic
    def patch(self, request, pk):
        job = get_object_or_404(Job.objects.select_for_update(), pk=pk)
        serializer = JobWriteSerializer(job, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop('comment', None)
        assignees = data.pop('assigned_to', None)

        before = audit.snapshot(job)
        old_status = job.status
        met_before = archive.meets_auto_rule(job)
        old_assignees = list(job.assigned_to.all())
        for field, value in data.items():
            setattr(job, field, value)
        apply_status_rules(job, old_status, 'completion_date' in data)

        changes = audit.diff(before, audit.snapshot(job))
        if assignees is not None and {u.pk for u in assignees} != {u.pk for u in old_assignees}:
            changes['assigned_to'] = audit.change('Assigned to', _names(old_assignees), _names(assignees))
            job.assigned_to.set(assignees)
            before_ids = {u.pk for u in old_assignees}
            queue_assignment_emails(job, [u for u in assignees if u.pk not in before_ids], request.user)
        event = None
        if changes:
            job.last_updated = timezone.now()
            job.save()
            AuditLog.record(request.user, AuditLog.UPDATED, job, changes=changes)
            event = archive.apply_auto_archive(job, met_before, request.user)
        data = job_detail(_jobs().get(pk=job.pk), JobSettings.load(), request.user)
        return Response({**data, 'archive_event': event})

    def delete(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        job.trash(request.user)
        logger.info(f'{request.user.username} moved {job.job_id} to the trash')
        return Response(status=status.HTTP_204_NO_CONTENT)


class JobArchiveView(archive.ArchiveView):
    section = 'jobs'

    def get_item(self, pk):
        return get_object_or_404(Job.objects.select_for_update(), pk=pk)


class JobUnarchiveView(JobArchiveView):
    archived = False


class JobBulkArchiveView(archive.BulkArchiveView):
    section = 'jobs'

    def find(self, ids):
        return Job.objects.select_for_update().in_bulk(ids)


class JobCommentsView(_JobView):
    dashboard_perms = {'POST': 'jobs.comment'}

    @transaction.atomic
    def post(self, request, pk):
        job = get_object_or_404(Job.objects.select_for_update(), pk=pk)
        serializer = CommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        now = timezone.now()
        comment = JobComment.objects.create(job=job, author=request.user, author_name=staff_name(request.user),
                                            body=serializer.validated_data['body'], created_at=now)
        job.last_updated = now
        job.save(update_fields=['last_updated'])
        audit.log(request.user, 'commented', job, summary=comment.body)
        return Response(comment_data(comment, request.user), status=status.HTTP_201_CREATED)


class JobCommentDetailView(_JobView):
    dashboard_perms = {'PATCH': 'jobs.comment'}

    @transaction.atomic
    def patch(self, request, pk, comment_pk):
        job = get_object_or_404(Job.objects.select_for_update(), pk=pk)
        comment = get_object_or_404(JobComment, pk=comment_pk, job=job)
        if comment.author_id != request.user.pk:
            return Response({'detail': 'You can only edit your own comments.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = CommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data['body']
        if body != comment.body:
            old = comment.body
            now = timezone.now()
            comment.body, comment.edited_at = body, now
            comment.save(update_fields=['body', 'edited_at'])
            job.last_updated = now
            job.save(update_fields=['last_updated'])
            audit.log(request.user, 'comment_edited', job, changes={'comment': audit.change('Comment', old, body)})
        return Response(comment_data(comment, request.user))


class JobOptionsView(_JobView):
    """Everything the job form and filters need, in one call."""

    dashboard_perms = {'GET': 'jobs.view'}

    def get(self, request):
        clients = client_queryset().select_related('client_profile')
        sites = {}
        for site in Site.objects.filter(is_active=True, client__in=clients).order_by('order', 'name'):
            sites.setdefault(site.client_id, []).append({'id': site.pk, 'name': site.name})
        thresholds = JobSettings.load()
        return Response({
            'categories': JobCategorySerializer(JobCategory.objects.all(), many=True).data,
            'statuses': [{'value': v, 'label': lab} for v, lab in Job.Status.choices],
            'payments': [{'value': v, 'label': lab} for v, lab in Job.Payment.choices],
            'staff': sorted(({'id': u.pk, 'name': staff_name(u)} for u in staff_queryset()),
                            key=lambda s: s['name'].lower()),
            'clients': sorted(({'id': u.pk, 'name': client_label(u), 'sites': sites.get(u.pk, [])}
                               for u in clients), key=lambda c: c['name'].lower()),
            'thresholds': JobSettingsSerializer(thresholds).data,
            'next_job_id': format_job_id(next_number()),
        })


class JobSettingsView(_JobView):
    dashboard_perms = {'GET': 'jobs.view', 'PATCH': 'jobs.settings'}

    def get(self, request):
        return Response(JobSettingsSerializer(JobSettings.load()).data)

    @transaction.atomic
    def patch(self, request):
        settings_row = JobSettings.load()
        serializer = JobSettingsSerializer(settings_row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = audit.snapshot(settings_row)
        serializer.save()
        audit.log_update(request.user, settings_row, before)
        return Response(serializer.data)


class JobCategoryListView(_JobView):
    dashboard_perms = {'POST': 'jobs.settings'}

    @transaction.atomic
    def post(self, request):
        serializer = JobCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        last = JobCategory.objects.aggregate(n=Max('order'))['n']
        category = serializer.save(order=serializer.validated_data.get('order', (last or 0) + 1))
        audit.log_create(request.user, category)
        return Response(JobCategorySerializer(category).data, status=status.HTTP_201_CREATED)


class JobCategoryDetailView(_JobView):
    dashboard_perms = {'PATCH': 'jobs.settings'}

    @transaction.atomic
    def patch(self, request, pk):
        category = get_object_or_404(JobCategory, pk=pk)
        serializer = JobCategorySerializer(category, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = audit.snapshot(category)
        serializer.save()
        audit.log_update(request.user, category, before)
        return Response(serializer.data)
