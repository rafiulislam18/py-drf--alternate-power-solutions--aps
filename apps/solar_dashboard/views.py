from django.db.models import Q
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.pagination import PageNumberPagination

from .models import SolarReport
from .serializers import SolarReportSerializer, SolarReportListSerializer


class ReportPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50


class SolarReportListCreateView(APIView):
    pagination_class = ReportPagination

    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request):
        search = request.query_params.get('search', '').strip()
        sort = request.query_params.get('sort', '-created_at')

        allowed_sorts = {
            'created_at': 'created_at',
            '-created_at': '-created_at',
            'client_name': 'client_name',
            '-client_name': '-client_name',
            'period_start': 'period_start',
            '-period_start': '-period_start',
        }
        order_by = allowed_sorts.get(sort, '-created_at')

        qs = SolarReport.objects.all().order_by(order_by)
        if search:
            qs = qs.filter(Q(client_name__icontains=search))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = SolarReportListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
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
            return SolarReport.objects.prefetch_related('sites').get(uuid=uuid)
        except SolarReport.DoesNotExist:
            return None

    def get(self, request, uuid):
        report = self.get_object(uuid)
        if report is None:
            return Response({'error': 'Report not found'}, status=status.HTTP_404_NOT_FOUND)
        serializer = SolarReportSerializer(report)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, uuid):
        report = self.get_object(uuid)
        if report is None:
            return Response({'error': 'Report not found'}, status=status.HTTP_404_NOT_FOUND)
        serializer = SolarReportSerializer(report, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, uuid):
        report = self.get_object(uuid)
        if report is None:
            return Response({'error': 'Report not found'}, status=status.HTTP_404_NOT_FOUND)
        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
