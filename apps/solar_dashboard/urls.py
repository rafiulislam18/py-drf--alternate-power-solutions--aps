from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    DashboardTokenObtainPairView,
    SolarReportListCreateView,
    SolarReportDetailView,
    SolarReportAggregateView,
    SolarReportDateRangeView,
    ClientListView,
    CreateClientView,
)

urlpatterns = [
    path('auth/token/', DashboardTokenObtainPairView.as_view(), name='dashboard-token-obtain'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='dashboard-token-refresh'),
    path('reports/', SolarReportListCreateView.as_view(), name='solar-report-list-create'),
    path('reports/aggregate/', SolarReportAggregateView.as_view(), name='solar-report-aggregate'),
    path('reports/<uuid:uuid>/', SolarReportDetailView.as_view(), name='solar-report-detail'),
    path('reports/<uuid:uuid>/date-range/', SolarReportDateRangeView.as_view(), name='solar-report-date-range'),
    path('clients/', ClientListView.as_view(), name='client-list'),
    path('clients/create/', CreateClientView.as_view(), name='client-create'),
]
