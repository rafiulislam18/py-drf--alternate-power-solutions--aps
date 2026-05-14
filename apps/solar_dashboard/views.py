from django.contrib.auth.models import User
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.models import ClientProfile
from .models import SolarReport
from .serializers import (
    SolarReportSerializer,
    SolarReportListSerializer,
    ClientUserSerializer,
    CreateClientUserSerializer,
)


def get_role(user):
    try:
        return user.client_profile.role
    except ClientProfile.DoesNotExist:
        return 'admin'  # Users without a profile are treated as admin (staff accounts)


class DashboardTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        role = get_role(self.user)
        data['role'] = role
        data['user_id'] = self.user.id
        try:
            profile = self.user.client_profile
            data['company_name'] = profile.company_name
            if profile.image:
                request = self.context.get('request')
                data['image'] = request.build_absolute_uri(profile.image.url) if request else profile.image.url
            else:
                data['image'] = None
        except ClientProfile.DoesNotExist:
            data['company_name'] = ''
            data['image'] = None
        return data


class DashboardTokenObtainPairView(TokenObtainPairView):
    serializer_class = DashboardTokenObtainPairSerializer


class ReportPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50


class SolarReportListCreateView(APIView):
    pagination_class = ReportPagination

    def get_permissions(self):
        return [IsAuthenticated()]

    def get(self, request):
        search = request.query_params.get('search', '').strip()
        sort = request.query_params.get('sort', '-created_at')

        allowed_sorts = {
            'created_at': 'created_at',
            '-created_at': '-created_at',
            'client_name': 'client__client_profile__company_name',
            '-client_name': '-client__client_profile__company_name',
            'period_start': 'period_start',
            '-period_start': '-period_start',
        }
        order_by = allowed_sorts.get(sort, '-created_at')

        qs = SolarReport.objects.select_related('client__client_profile').order_by(order_by)

        role = get_role(request.user)
        if role == 'client':
            qs = qs.filter(client=request.user)

        if search:
            qs = qs.filter(
                Q(client__client_profile__company_name__icontains=search) |
                Q(client__username__icontains=search)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = SolarReportListSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        if get_role(request.user) != 'admin':
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        serializer = SolarReportSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SolarReportDetailView(APIView):
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_object(self, uuid):
        try:
            return SolarReport.objects.prefetch_related('sites').select_related('client__client_profile').get(uuid=uuid)
        except SolarReport.DoesNotExist:
            return None

    def get(self, request, uuid):
        report = self.get_object(uuid)
        if report is None:
            return Response({'error': 'Report not found'}, status=status.HTTP_404_NOT_FOUND)
        serializer = SolarReportSerializer(report, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, uuid):
        report = self.get_object(uuid)
        if report is None:
            return Response({'error': 'Report not found'}, status=status.HTTP_404_NOT_FOUND)
        if get_role(request.user) != 'admin':
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        serializer = SolarReportSerializer(report, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, uuid):
        report = self.get_object(uuid)
        if report is None:
            return Response({'error': 'Report not found'}, status=status.HTTP_404_NOT_FOUND)
        if get_role(request.user) != 'admin':
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClientListView(APIView):
    """Returns all client users (those with a ClientProfile role=client) for the dropdown."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if get_role(request.user) != 'admin':
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        clients = User.objects.filter(
            client_profile__role='client'
        ).select_related('client_profile').order_by('client_profile__company_name', 'username')
        serializer = ClientUserSerializer(clients, many=True, context={'request': request})
        return Response(serializer.data)


class CreateClientView(APIView):
    """Admin creates a new client account."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if get_role(request.user) != 'admin':
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        serializer = CreateClientUserSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(
                ClientUserSerializer(user, context={'request': request}).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
