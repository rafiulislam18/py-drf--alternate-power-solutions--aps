"""
Operations jobs API (mounted at ``/jobs/``) — staff only, per permission
(``apps.staff.catalogue`` section ``jobs``).

    GET    /jobs/                  list: ?status &category &payment &assigned=<id>|me|none
                                   &attention=1 &q &ordering=default|stale|unpaid|newest|number &page
    POST   /jobs/                  create (jobs.create; + jobs.assign to assign people)
    GET    /jobs/<id>/             one job with its comments
    PATCH  /jobs/<id>/             edit (jobs.edit; jobs.assign for ``assigned_to``)
    DELETE /jobs/<id>/             move to the trash (jobs.delete)
    POST   /jobs/<id>/comments/    add a comment (jobs.comment)
    PATCH  /jobs/<id>/comments/<c>/ edit your own comment (jobs.comment)
    GET    /jobs/options/          categories, statuses, staff, clients + sites, colour thresholds
    GET    /jobs/settings/         colour thresholds; PATCH needs jobs.settings
    POST   /jobs/categories/       add a category (jobs.settings)
    PATCH  /jobs/categories/<id>/  rename / reorder / switch off (jobs.settings) - never deleted

Any change to a job (field, assigned staff, comment) moves its "last updated"
and is written to the audit history.
"""

import logging
from datetime import datetime, time, timedelta
from functools import partial

from django.db import IntegrityError, transaction
from django.db.models import Case, Count, DateField, F, IntegerField, Max, Prefetch, Q, Value, When
from django.db.models.functions import Coalesce, TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.records import audit
from apps.records.models import AuditLog
from apps.staff.permissions import HasDashboardPerm
from apps.solar_dashboard.models import Site
from utils.exceptions import custom_exception_handler

from .models import Job, JobCategory, JobComment, JobSettings, format_job_id, staff_name
from .serializers import (
    CommentSerializer, JobCategorySerializer, JobSettingsSerializer, JobWriteSerializer, client_label, client_queryset, comment_data,
    job_detail, job_row, staff_queryset,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
STATUS_RANK = Case(*[When(status=s, then=Value(i)) for i, s in enumerate(Job.Status.values)],
                   default=Value(len(Job.Status.values)), output_field=IntegerField())
NEWEST = [F('date_added').desc(nulls_last=True), '-last_updated', '-number']
# Done / invoiced but not marked Paid (unpaid, paid deposit or not set) — waiting since the
# completion date (or the last update when there's none), like the sheet's unpaid alert.
UNPAID = Q(status__in=Job.FINISHED) & ~Q(payment=Job.Payment.PAID)
UNPAID_SINCE = Case(When(UNPAID, then=Coalesce('completion_date', TruncDate('last_updated'))),
                    default=None, output_field=DateField())
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
    return Job.objects.select_related('category', 'client__client_profile', 'site', 'created_by').prefetch_related(
        Prefetch('assigned_to', queryset=staff_queryset().order_by('first_name', 'username')),
        Prefetch('comments', queryset=JobComment.objects.order_by('created_at', 'id')),
    )


def next_number():
    """One more than the highest number ever given (jobs in the trash included)."""
    return (Job.all_objects.aggregate(n=Max('number'))['n'] or 0) + 1


def watch_cutoff(thresholds):
    """Jobs last updated before this are ``watch_days`` or more days old (by local date)."""
    day = timezone.localdate() - timedelta(days=thresholds.watch_days - 1)
    return timezone.make_aware(datetime.combine(day, time.min))


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
        qs = _jobs()
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
            qs = qs.filter(status__in=Job.LIVE, last_updated__lt=watch_cutoff(thresholds))

        # Counts per status for the filter, before the status filter itself.
        found = dict(qs.order_by().values_list('status').annotate(n=Count('id', distinct=True)))
        counts = {s: found.get(s, 0) for s in Job.Status.values}
        counts['all'] = sum(counts.values())

        statuses = [s for s in (p.get('status') or '').split(',') if s in Job.Status.values]
        if statuses:
            qs = qs.filter(status__in=statuses)

        qs = qs.order_by(*ORDERINGS.get(p.get('ordering') or 'default', ORDERINGS['default']))
        total = qs.count()
        pages = max(1, -(-total // PAGE_SIZE))
        page = min(max(1, _int(p.get('page')) or 1), pages)
        today = timezone.localdate()
        rows = [job_row(j, thresholds, today) for j in qs[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]]
        return Response({'results': rows, 'count': total, 'page': page, 'total_pages': pages, 'counts': counts})

    def post(self, request):
        serializer = JobWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        assignees = data.pop('assigned_to', [])
        comment = (data.pop('comment', '') or '').strip()

        for attempt in range(5):  # two people saving at once may race for a number
            try:
                with transaction.atomic():
                    job = Job(**data, number=next_number(), created_by=request.user)
                    apply_status_rules(job, Job.Status.OPEN, 'completion_date' in data)
                    job.save()
                    break
            except IntegrityError:
                if attempt == 4:
                    raise
        with transaction.atomic():
            job.assigned_to.set(assignees)
            audit.log_create(request.user, job, summary=f'Assigned to {_names(assignees)}' if assignees else '')
            if comment:
                c = JobComment.objects.create(job=job, author=request.user, author_name=staff_name(request.user),
                                              body=comment)
                audit.log(request.user, 'commented', job, summary=c.body)
        logger.info(f'{request.user.username} created {job.job_id}')
        return Response(job_detail(_jobs().get(pk=job.pk), JobSettings.load(), request.user),
                        status=status.HTTP_201_CREATED)


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
        old_assignees = list(job.assigned_to.all())
        for field, value in data.items():
            setattr(job, field, value)
        apply_status_rules(job, old_status, 'completion_date' in data)

        changes = audit.diff(before, audit.snapshot(job))
        if assignees is not None and {u.pk for u in assignees} != {u.pk for u in old_assignees}:
            changes['assigned_to'] = audit.change('Assigned to', _names(old_assignees), _names(assignees))
            job.assigned_to.set(assignees)
        if changes:
            job.last_updated = timezone.now()
            job.save()
            AuditLog.record(request.user, AuditLog.UPDATED, job, changes=changes)
        return Response(job_detail(_jobs().get(pk=job.pk), JobSettings.load(), request.user))

    def delete(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        job.trash(request.user)
        logger.info(f'{request.user.username} moved {job.job_id} to the trash')
        return Response(status=status.HTTP_204_NO_CONTENT)


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
