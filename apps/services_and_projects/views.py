from functools import partial

from django.db.models import Count
from rest_framework.views import APIView
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.core.content_files import delete_unused_files, file_names
from apps.core.permissions import IsDashboardAdmin
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
    permission_classes = [IsAuthenticated, IsDashboardAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    # Content lists are small; return them whole (no settings-level paging).
    pagination_class = None

    def get_exception_handler(self):
        # Keep field names alongside "detail" so the editor can highlight inputs.
        return partial(custom_exception_handler, keep_fields=True)


class AdminServiceListCreateView(_AdminContentMixin, generics.ListCreateAPIView):
    serializer_class = AdminServiceSerializer
    queryset = Service.objects.annotate(num_projects=Count('projects')).order_by('-appreciation_mark', 'title')


PROJECT_IMAGE_FIELDS = ['image', 'image_2', 'image_3']


class AdminServiceDetailView(_AdminContentMixin, generics.RetrieveUpdateDestroyAPIView):
    """Deleting a service also deletes its projects (FK cascade)."""
    serializer_class = AdminServiceSerializer
    queryset = Service.objects.annotate(num_projects=Count('projects'))

    def perform_update(self, serializer):
        before = file_names(serializer.instance, ['image'])
        instance = serializer.save()
        delete_unused_files(Service, ['image'], before - file_names(instance, ['image']))

    def perform_destroy(self, instance):
        service_files = file_names(instance, ['image'])
        project_files = set()
        for project in instance.projects.all():
            project_files |= file_names(project, PROJECT_IMAGE_FIELDS)
        instance.delete()
        delete_unused_files(Service, ['image'], service_files)
        delete_unused_files(Project, PROJECT_IMAGE_FIELDS, project_files)


class AdminProjectListCreateView(_AdminContentMixin, generics.ListCreateAPIView):
    serializer_class = AdminProjectSerializer

    def get_queryset(self):
        qs = Project.objects.select_related('service').order_by('-appreciation_mark', 'title')
        service = self.request.query_params.get('service')
        if service and service.isdigit():
            qs = qs.filter(service_id=service)
        return qs


class AdminProjectDetailView(_AdminContentMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AdminProjectSerializer
    queryset = Project.objects.select_related('service')

    def perform_update(self, serializer):
        before = file_names(serializer.instance, PROJECT_IMAGE_FIELDS)
        instance = serializer.save()
        delete_unused_files(Project, PROJECT_IMAGE_FIELDS, before - file_names(instance, PROJECT_IMAGE_FIELDS))

    def perform_destroy(self, instance):
        files = file_names(instance, PROJECT_IMAGE_FIELDS)
        instance.delete()
        delete_unused_files(Project, PROJECT_IMAGE_FIELDS, files)
