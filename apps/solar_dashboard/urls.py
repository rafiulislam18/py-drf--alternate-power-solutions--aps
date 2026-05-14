from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    DashboardTokenObtainPairView,
    SolarReportListCreateView,
    SolarReportDetailView,
    ClientListView,
    CreateClientView,
)

urlpatterns = [
    path('auth/token/', DashboardTokenObtainPairView.as_view(), name='dashboard-token-obtain'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='dashboard-token-refresh'),
    path('reports/', SolarReportListCreateView.as_view(), name='solar-report-list-create'),
    path('reports/<uuid:uuid>/', SolarReportDetailView.as_view(), name='solar-report-detail'),
    path('clients/', ClientListView.as_view(), name='client-list'),
    path('clients/create/', CreateClientView.as_view(), name='client-create'),
]
