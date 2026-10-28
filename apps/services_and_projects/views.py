from functools import partial

from django.db.models import Count, Q
from rest_framework.views import APIView
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.core.content_files import delete_unused_files, file_names
from apps.records import audit
from apps.staff.permissions import HasDashboardPerm
from utils.exceptions import custom_exception_handler
from .models import *
from .serializers import *


class ServiceListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        services = Service.objects.all().order_by("-appreciation_mark")
        serializer = ServiceListSerializer(services, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ServiceProjectListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        services = Service.objects.all().order_by("-appreciation_mark")
        serializer = ServiceSerializer(services, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ServiceDetailsAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, service_id, *args, **kwargs):
        try:
            service = Service.objects.get(id=service_id)
            serializer = ServiceSerializer(service)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Service.DoesNotExist:
            return Response(
                {"error": "Service not found"},
                status=status.HTTP_404_NOT_FOUND
            )


class ProjectDetailsAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, project_id, *args, **kwargs):
        try:
            project = Project.objects.get(id=project_id)

            # Get the service and its top 3 projects
            service = project.service
            top_projects = service.projects.exclude(id=project_id).order_by('-appreciation_mark')[:3]

            # Create custom response data
            response_data = ProjectSerializer(project).data
            response_data['service'] = ServiceSerializer(service).data
            response_data['service']['top_projects'] = ProjectSerializer(top_projects, many=True).data

            return Response(response_data, status=status.HTTP_200_OK)
        except Project.DoesNotExist:
            return Response(
                {"error": "Project not found"},
                status=status.HTTP_404_NOT_FOUND
            )


# ---------------------------------------------------------------------------
# Dashboard "Site content" editor — admin-only CRUD. Multipart so images can
# be uploaded; `features` is sent as a JSON-encoded list in the form data.
# ---------------------------------------------------------------------------

class _AdminContentMixin:
    permission_classes = [IsAuthenticated, HasDashboardPerm]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    # Content lists are small; return them whole (no settings-level paging).
    pagination_class = None

    def get_exception_handler(self):
        # Keep field names alongside "detail" so the editor can highlight inputs.
        return partial(custom_exception_handler, keep_fields=True)


# Projects in the trash don't count.
_LIVE_PROJECTS = Count('projects', filter=Q(projects__deleted_at__isnull=True))


class AdminServiceListCreateView(_AdminContentMixin, generics.ListCreateAPIView):
    dashboard_section = 'services'
    serializer_class = AdminServiceSerializer
    queryset = Service.objects.annotate(num_projects=_LIVE_PROJECTS).order_by('-appreciation_mark', 'title')

    def perform_create(self, serializer):
        audit.log_create(self.request.user, serializer.save())


PROJECT_IMAGE_FIELDS = ['image', 'image_2', 'image_3']


class AdminServiceDetailView(_AdminContentMixin, generics.RetrieveUpdateDestroyAPIView):
    """Deleting a service moves it — and its projects — to the trash (images kept until it's deleted for good)."""
    dashboard_section = 'services'
    serializer_class = AdminServiceSerializer
    queryset = Service.objects.annotate(num_projects=_LIVE_PROJECTS)

    def perform_update(self, serializer):
        before, snap = file_names(serializer.instance, ['image']), audit.snapshot(serializer.instance)
        instance = serializer.save()
        delete_unused_files(Service, ['image'], before - file_names(instance, ['image']))
        audit.log_update(self.request.user, instance, snap)

    def perform_destroy(self, instance):
        instance.trash(self.request.user)


class AdminProjectListCreateView(_AdminContentMixin, generics.ListCreateAPIView):
    dashboard_section = 'projects'
    serializer_class = AdminProjectSerializer

    def get_queryset(self):
        qs = Project.objects.select_related('service').order_by('-appreciation_mark', 'title')
        service = self.request.query_params.get('service')
        if service and service.isdigit():
            qs = qs.filter(service_id=service)
        return qs

    def perform_create(self, serializer):
        audit.log_create(self.request.user, serializer.save())


class AdminProjectDetailView(_AdminContentMixin, generics.RetrieveUpdateDestroyAPIView):
    dashboard_section = 'projects'
    serializer_class = AdminProjectSerializer
    queryset = Project.objects.select_related('service')

    def perform_update(self, serializer):
        before, snap = file_names(serializer.instance, PROJECT_IMAGE_FIELDS), audit.snapshot(serializer.instance)
        instance = serializer.save()
        delete_unused_files(Project, PROJECT_IMAGE_FIELDS, before - file_names(instance, PROJECT_IMAGE_FIELDS))
        audit.log_update(self.request.user, instance, snap)

    def perform_destroy(self, instance):
        instance.trash(self.request.user)  # images kept until it's deleted for good
