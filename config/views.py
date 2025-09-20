from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from apps.services_and_projects.models import Service, Project
from .serializers import HomePageServiceSerializer, HomePageProjectSerializer


class HomePageAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        services = Service.objects.all().order_by("-appreciation_mark")
        projects = Project.objects.all().order_by("-appreciation_mark")[:3]
        return Response(
            {
                "services": HomePageServiceSerializer(services, many=True).data,
                "projects": HomePageProjectSerializer(projects, many=True).data
            },
            status=status.HTTP_200_OK
        )
