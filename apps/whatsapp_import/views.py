"""
API for the WhatsApp message review page.

Admin-only, behind the same JWT auth as the solar dashboard. The ops manager
lists messages (filterable by chat), and marks/unmarks them as jobs — per
message or in bulk. Marking stamps `marked_as_job_at`; a later step pushes
newly-marked, not-yet-exported messages into the jobs spreadsheet.
"""

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.solar_dashboard.views import get_role
from .jobs_export import JobsSheetConfigError, export_marked_jobs, pending_jobs_qs
from .models import WhatsAppMessage
from .serializers import (
    BulkDismissSerializer, BulkMarkJobSerializer, DismissSerializer,
    MarkJobSerializer, WhatsAppMessageSerializer,
)


class MessagePagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


def _require_admin(request):
    """Return None if admin, else a 403 Response."""
    if get_role(request.user) != 'admin':
        return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
    return None


class WhatsAppMessageListView(APIView):
    """
    GET list of messages (admin only), newest first.

    Query params:
      chat      — filter to one conversation (exact chat_name)
      status    — one of:
                    'unmarked'  (default) not a job AND not dismissed — the triage queue
                    'marked'    marked as a job
                    'dismissed' explicitly marked "not a job"
                    'all'       everything, including dismissed
                  Note: every view EXCEPT 'dismissed' and 'all' hides dismissed
                  messages, so dismissed noise stays out of the working view.
      search    — substring match on text or sender
      page, page_size
    """
    permission_classes = [IsAuthenticated]
    pagination_class = MessagePagination

    def get(self, request):
        forbidden = _require_admin(request)
        if forbidden:
            return forbidden

        qs = WhatsAppMessage.objects.all()

        chat = request.query_params.get('chat', '').strip()
        if chat:
            qs = qs.filter(chat_name=chat)

        job_status = request.query_params.get('status', 'unmarked')
        if job_status == 'marked':
            qs = qs.filter(marked_as_job=True, dismissed=False)
        elif job_status == 'dismissed':
            qs = qs.filter(dismissed=True)
        elif job_status == 'all':
            pass  # everything, dismissed included
        else:  # 'unmarked' — the default triage queue
            qs = qs.filter(marked_as_job=False, dismissed=False)

        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(text__icontains=search) | Q(sender__icontains=search))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = WhatsAppMessageSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class WhatsAppStatusCountsView(APIView):
    """GET the per-status message tallies for the filter tabs (respects `chat`)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        forbidden = _require_admin(request)
        if forbidden:
            return forbidden

        qs = WhatsAppMessage.objects.all()
        chat = request.query_params.get('chat', '').strip()
        if chat:
            qs = qs.filter(chat_name=chat)

        return Response({
            'unmarked': qs.filter(marked_as_job=False, dismissed=False).count(),
            'marked': qs.filter(marked_as_job=True, dismissed=False).count(),
            'dismissed': qs.filter(dismissed=True).count(),
            'all': qs.count(),
        })


class WhatsAppChatListView(APIView):
    """GET the distinct chat names (for the filter dropdown), with counts."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        forbidden = _require_admin(request)
        if forbidden:
            return forbidden

        from django.db.models import Count
        chats = (
            WhatsAppMessage.objects
            .values('chat_name')
            .annotate(count=Count('id'))
            .order_by('chat_name')
        )
        return Response(list(chats))


class WhatsAppMessageMarkView(APIView):
    """PATCH a single message's job flag."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        forbidden = _require_admin(request)
        if forbidden:
            return forbidden

        try:
            message = WhatsAppMessage.objects.get(pk=pk)
        except WhatsAppMessage.DoesNotExist:
            return Response({'detail': 'Message not found'}, status=status.HTTP_404_NOT_FOUND)

        serializer = MarkJobSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _apply_mark(message, serializer.validated_data['marked_as_job'])
        message.save(update_fields=['marked_as_job', 'marked_as_job_at'])
        return Response(WhatsAppMessageSerializer(message).data, status=status.HTTP_200_OK)


class WhatsAppMessageBulkMarkView(APIView):
    """POST to mark/unmark several messages at once."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        forbidden = _require_admin(request)
        if forbidden:
            return forbidden

        serializer = BulkMarkJobSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data['ids']
        mark = serializer.validated_data['marked_as_job']

        now = timezone.now()
        if mark:
            # Only stamp marked_as_job_at on messages that weren't already marked.
            updated = (WhatsAppMessage.objects
                       .filter(pk__in=ids, marked_as_job=False)
                       .update(marked_as_job=True, marked_as_job_at=now))
        else:
            # Don't unmark ones already pushed to the sheet (would desync a job).
            updated = (WhatsAppMessage.objects
                       .filter(pk__in=ids, exported_to_jobs_sheet=False)
                       .update(marked_as_job=False, marked_as_job_at=None))
        return Response({'updated': updated}, status=status.HTTP_200_OK)


class WhatsAppMessageDismissView(APIView):
    """PATCH a single message's 'not a job' dismissal (reversible)."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        forbidden = _require_admin(request)
        if forbidden:
            return forbidden

        try:
            message = WhatsAppMessage.objects.get(pk=pk)
        except WhatsAppMessage.DoesNotExist:
            return Response({'detail': 'Message not found'}, status=status.HTTP_404_NOT_FOUND)

        serializer = DismissSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dismiss = serializer.validated_data['dismissed']

        if dismiss:
            message.dismissed = True
            message.dismissed_at = timezone.now()
            # Dismissing something as "not a job" implies it isn't a job — clear
            # the job flag too, but never touch one already exported to the sheet.
            if message.marked_as_job and not message.exported_to_jobs_sheet:
                message.marked_as_job = False
                message.marked_as_job_at = None
        else:
            message.dismissed = False
            message.dismissed_at = None
        message.save(update_fields=['dismissed', 'dismissed_at', 'marked_as_job', 'marked_as_job_at'])
        return Response(WhatsAppMessageSerializer(message).data, status=status.HTTP_200_OK)


class WhatsAppMessageBulkDismissView(APIView):
    """POST to dismiss/un-dismiss several messages at once."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        forbidden = _require_admin(request)
        if forbidden:
            return forbidden

        serializer = BulkDismissSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data['ids']
        dismiss = serializer.validated_data['dismissed']

        now = timezone.now()
        if dismiss:
            updated = (WhatsAppMessage.objects
                       .filter(pk__in=ids, dismissed=False)
                       .update(dismissed=True, dismissed_at=now))
            # Clear job flag on newly-dismissed ones not yet exported.
            (WhatsAppMessage.objects
             .filter(pk__in=ids, marked_as_job=True, exported_to_jobs_sheet=False)
             .update(marked_as_job=False, marked_as_job_at=None))
        else:
            updated = (WhatsAppMessage.objects
                       .filter(pk__in=ids, dismissed=True)
                       .update(dismissed=False, dismissed_at=None))
        return Response({'updated': updated}, status=status.HTTP_200_OK)


class WhatsAppJobsExportView(APIView):
    """
    GET  — how many marked messages are pending push to the jobs sheet.
    POST — push them now (the "Push to jobs sheet" button). Admin only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        forbidden = _require_admin(request)
        if forbidden:
            return forbidden
        return Response({'pending': pending_jobs_qs().count()})

    def post(self, request):
        forbidden = _require_admin(request)
        if forbidden:
            return forbidden
        try:
            stats = export_marked_jobs()
        except JobsSheetConfigError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        if stats.get('error'):
            return Response(
                {'detail': f"Jobs sheet error: {stats['error']}", **stats},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(stats, status=status.HTTP_200_OK)


def _apply_mark(message, mark):
    """Set the job flag + timestamp on an in-memory message instance."""
    if mark and not message.marked_as_job:
        message.marked_as_job = True
        message.marked_as_job_at = timezone.now()
    elif not mark:
        # Guard: refuse to unmark something already exported to the sheet.
        if message.exported_to_jobs_sheet:
            return
        message.marked_as_job = False
        message.marked_as_job_at = None
