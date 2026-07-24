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
from .models import SolarReport, SiteData, Site
from .serializers import (
    SolarReportSerializer,
    SolarReportListSerializer,
    ClientUserSerializer,
    CreateClientUserSerializer,
    SiteSerializer,
)


# The numeric metrics summed per site across a date range. (The old hardcoded
# client name + site-name list are gone — the aggregate view now works for any
# client using their real Site records.)
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


class SiteListCreateView(APIView):
    """Admin-only: list a client's sites, or create one.

    GET  /dashboard/sites/?client_id=<id>[&include_inactive=1]
    POST /dashboard/sites/   {client_id, name, has_battery}
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if get_role(request.user) != 'admin':
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        qs = Site.objects.select_related('client__client_profile')
        client_id = request.query_params.get('client_id')
        if client_id:
            qs = qs.filter(client_id=client_id)
        if request.query_params.get('include_inactive') not in ('1', 'true', 'yes'):
            qs = qs.filter(is_active=True)
        qs = qs.order_by('order', 'name')

        serializer = SiteSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request):
        if get_role(request.user) != 'admin':
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        serializer = SiteSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SiteDetailView(APIView):
    """Admin-only: edit or retire a single site.

    PATCH  /dashboard/sites/<pk>/   (partial update; e.g. rename, toggle is_active)
    DELETE /dashboard/sites/<pk>/   (soft-retire by default; hard-delete blocked if
                                     the site has report history)
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return Site.objects.select_related('client__client_profile').get(pk=pk)
        except Site.DoesNotExist:
            return None

    def patch(self, request, pk):
        if get_role(request.user) != 'admin':
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        site = self.get_object(pk)
        if site is None:
            return Response({'error': 'Site not found'}, status=status.HTTP_404_NOT_FOUND)
        serializer = SiteSerializer(site, data=request.data, partial=True,
                                    context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        if get_role(request.user) != 'admin':
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        site = self.get_object(pk)
        if site is None:
            return Response({'error': 'Site not found'}, status=status.HTTP_404_NOT_FOUND)

        # Protect report history: if any SiteData references this site, refuse a hard
        # delete and retire it instead (soft-delete).
        if site.data_rows.exists():
            site.is_active = False
            site.save(update_fields=['is_active', 'updated_at'])
            return Response(
                {'detail': 'Site has report history; it was retired (is_active=False) '
                           'instead of deleted.',
                 'retired': True},
                status=status.HTTP_200_OK,
            )

        site.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _parse_date(value):
    try:
        y, m, d = value.split('-')
        return date(int(y), int(m), int(d))
    except Exception:
        return None


class SolarReportAggregateView(APIView):
    """Aggregate per-site values across reports overlapping [from, to] with
    pro-rata clipping plus daily-average extrapolation for uncovered days.

    Public (matches detail GET) so shared dashboard links keep working.

    Works for ANY client: the site list comes from that client's real Site records
    (active sites, UNION any site — active or retired — that has data within the
    selected range). Sites are matched by site_id, so renaming a site never splits
    its history. An active site with no data in the range shows as an all-zero row;
    a retired site with no data in the range is omitted.

    The aggregation math (pro-rata clipping + extrapolation) is unchanged.
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

        client = anchor.client
        if client is None:
            return Response({'error': 'Aggregation requires a client-linked report'},
                            status=status.HTTP_400_BAD_REQUEST)

        from_d = _parse_date(from_str)
        to_d = _parse_date(to_str)
        if not from_d or not to_d or from_d > to_d:
            return Response({'error': 'Invalid date range'}, status=status.HTTP_400_BAD_REQUEST)

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
            .prefetch_related('sites__site')
        )

        # Accumulate totals keyed by site_id (rename-safe). We also remember each
        # site's Site record so we can output name/battery/order and decide which
        # sites appear.
        per_site_totals = {}       # site_id -> {field: Decimal}
        sites_with_data = {}       # site_id -> Site instance (seen in range)
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

            for row in report.sites.all():
                site = row.site
                if site is None:
                    continue  # legacy row not linked to a Site; skip defensively
                sid = site.id
                sites_with_data[sid] = site
                totals = per_site_totals.setdefault(
                    sid, {f: Decimal('0') for f in AGGREGATE_NUMERIC_FIELDS})
                for field in AGGREGATE_NUMERIC_FIELDS:
                    val = getattr(row, field) or Decimal('0')
                    totals[field] += val * fraction

        # The display set: all currently-active sites, plus any site (active or
        # retired) that had data within the range.
        display_sites = {s.id: s for s in Site.objects.filter(client=client, is_active=True)}
        display_sites.update(sites_with_data)  # retired-but-has-data get included

        ordered_sites = sorted(
            display_sites.values(),
            key=lambda s: (s.order, s.name),
        )

        total_range_days = (to_d - from_d).days + 1
        covered_count = len(covered_days)
        uncovered_count = total_range_days - covered_count

        empty = {f: Decimal('0') for f in AGGREGATE_NUMERIC_FIELDS}
        sites_out = []
        for order_idx, site in enumerate(ordered_sites):
            totals = per_site_totals.get(site.id, empty)
            site_obj = {
                'id': site.id,
                'order': order_idx,
                'site_name': site.name,
                'has_battery': site.has_battery,
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

        if anchor.client is None:
            return Response({'error': 'Report has no client'}, status=status.HTTP_400_BAD_REQUEST)

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
