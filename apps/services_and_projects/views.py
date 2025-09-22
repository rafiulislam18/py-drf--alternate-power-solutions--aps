from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
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
