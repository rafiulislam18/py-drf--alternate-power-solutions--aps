from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Q, Min, Max
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.models import ClientProfile
from .models import SolarReport, SiteData
from .serializers import (
    SolarReportSerializer,
    SolarReportListSerializer,
    ClientUserSerializer,
    CreateClientUserSerializer,
)


# Urgent, non-scalable: feature is gated to this client only.
AGGREGATE_CLIENT_COMPANY_NAME = 'Urban Growth'
AGGREGATE_SITE_NAMES = [
    'Paarl – Units 8 & 9',
    'Paarl* (with battery)',
    'Stuart Close',
    'Springfield – Philippi',
    'Rialto (with battery)',
    'Izuzu',
    'Henry Vos',
]
AGGREGATE_NUMERIC_FIELDS = [
    'solar_yield', 'battery_charge', 'usable_solar',
    'estimated_saving', 'used_from_battery',
    'sell_to_grid_kwh', 'sell_to_grid_r',
    'grid_consumption', 'total_consumption',
]


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
                data['image'] = profile.image.url
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
            'created_at': 'report_date',
            '-created_at': '-report_date',
            'client_name': 'client__client_profile__company_name',
            '-client_name': '-client__client_profile__company_name',
        }
        order_by = allowed_sorts.get(sort, '-report_date')

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


def _parse_date(value):
    try:
        y, m, d = value.split('-')
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def _is_aggregate_client(client_user):
    if not client_user:
        return False
    try:
        return client_user.client_profile.company_name == AGGREGATE_CLIENT_COMPANY_NAME
    except ClientProfile.DoesNotExist:
        return False


class SolarReportAggregateView(APIView):
    """Aggregate per-site values across reports overlapping [from, to] with
    pro-rata clipping plus daily-average extrapolation for uncovered days.

    Public (matches detail GET) so shared dashboard links keep working.
    Hardcoded to the 'Urban Growth' client and their 7 known sites.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        report_uuid = request.query_params.get('report_uuid')
        from_str = request.query_params.get('from')
        to_str = request.query_params.get('to')

        if not (report_uuid and from_str and to_str):
            return Response(
                {'error': 'report_uuid, from, and to are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            anchor = SolarReport.objects.select_related('client__client_profile').get(uuid=report_uuid)
        except SolarReport.DoesNotExist:
            return Response({'error': 'Report not found'}, status=status.HTTP_404_NOT_FOUND)

        if not _is_aggregate_client(anchor.client):
            return Response({'error': 'Aggregation not available for this client'}, status=status.HTTP_403_FORBIDDEN)

        from_d = _parse_date(from_str)
        to_d = _parse_date(to_str)
        if not from_d or not to_d or from_d > to_d:
            return Response({'error': 'Invalid date range'}, status=status.HTTP_400_BAD_REQUEST)

        client = anchor.client
        bounds = SolarReport.objects.filter(client=client).aggregate(
            min_start=Min('period_start'),
            max_end=Max('period_end'),
        )
        min_start = bounds['min_start']
        max_end = bounds['max_end']
        if not min_start or not max_end:
            return Response({'error': 'No data for client'}, status=status.HTTP_404_NOT_FOUND)

        if from_d < min_start:
            from_d = min_start
        if to_d > max_end:
            to_d = max_end
        if from_d > to_d:
            return Response({'error': 'Range outside available data'}, status=status.HTTP_400_BAD_REQUEST)

        reports = (
            SolarReport.objects
            .filter(client=client, period_start__lte=to_d, period_end__gte=from_d)
            .prefetch_related('sites')
        )

        per_site_totals = {
            name: {f: Decimal('0') for f in AGGREGATE_NUMERIC_FIELDS}
            for name in AGGREGATE_SITE_NAMES
        }
        per_site_has_battery = {name: False for name in AGGREGATE_SITE_NAMES}
        covered_days = set()

        for report in reports:
            r_start = max(report.period_start, from_d)
            r_end = min(report.period_end, to_d)
            if r_start > r_end:
                continue
            overlap_days = (r_end - r_start).days + 1
            report_days = (report.period_end - report.period_start).days + 1
            if report_days <= 0:
                continue
            fraction = Decimal(overlap_days) / Decimal(report_days)

            for i in range(overlap_days):
                covered_days.add(r_start + timedelta(days=i))

            for site in report.sites.all():
                if site.site_name not in per_site_totals:
                    continue
                if site.has_battery:
                    per_site_has_battery[site.site_name] = True
                for field in AGGREGATE_NUMERIC_FIELDS:
                    val = getattr(site, field) or Decimal('0')
                    per_site_totals[site.site_name][field] += val * fraction

        total_range_days = (to_d - from_d).days + 1
        covered_count = len(covered_days)
        uncovered_count = total_range_days - covered_count

        sites_out = []
        for order_idx, name in enumerate(AGGREGATE_SITE_NAMES):
            totals = per_site_totals[name]
            site_obj = {
                'id': order_idx,
                'order': order_idx,
                'site_name': name,
                'has_battery': per_site_has_battery[name],
            }
            for field in AGGREGATE_NUMERIC_FIELDS:
                covered_total = totals[field]
                if covered_count > 0 and uncovered_count > 0:
                    daily_avg = covered_total / Decimal(covered_count)
                    final = covered_total + daily_avg * Decimal(uncovered_count)
                else:
                    final = covered_total
                site_obj[field] = str(final.quantize(Decimal('0.01')))
            sites_out.append(site_obj)

        client_data = ClientUserSerializer(client, context={'request': request}).data
        sibling_reports = list(
            SolarReport.objects
            .filter(client=client)
            .order_by('-period_start')
            .values('uuid', 'period_start', 'period_end')
        )
        sibling_reports = [
            {'uuid': str(r['uuid']), 'period_start': r['period_start'], 'period_end': r['period_end']}
            for r in sibling_reports
        ]

        return Response({
            'uuid': str(anchor.uuid),
            'client': client_data,
            'report_date': anchor.report_date,
            'period_start': from_d,
            'period_end': to_d,
            'created_at': anchor.created_at,
            'sites': sites_out,
            'sibling_reports': sibling_reports,
            'is_estimate': True,
            'covered_days': covered_count,
            'uncovered_days': uncovered_count,
        })


class SolarReportDateRangeView(APIView):
    """Returns the min period_start and max period_end across the client's
    reports — used by the frontend to clamp date pickers.

    Public to support shared dashboard links.
    """
    permission_classes = [AllowAny]

    def get(self, request, uuid):
        try:
            anchor = SolarReport.objects.select_related('client__client_profile').get(uuid=uuid)
        except SolarReport.DoesNotExist:
            return Response({'error': 'Report not found'}, status=status.HTTP_404_NOT_FOUND)

        if not _is_aggregate_client(anchor.client):
            return Response({'error': 'Not available for this client'}, status=status.HTTP_403_FORBIDDEN)

        bounds = SolarReport.objects.filter(client=anchor.client).aggregate(
            min_start=Min('period_start'),
            max_end=Max('period_end'),
        )
        if not bounds['min_start']:
            return Response({'error': 'No data'}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'min_date': bounds['min_start'],
            'max_date': bounds['max_end'],
        })
