from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import SolarReportListCreateView, SolarReportDetailView

urlpatterns = [
    path('auth/token/', TokenObtainPairView.as_view(), name='dashboard-token-obtain'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='dashboard-token-refresh'),
    path('reports/', SolarReportListCreateView.as_view(), name='solar-report-list-create'),
    path('reports/<uuid:uuid>/', SolarReportDetailView.as_view(), name='solar-report-detail'),
]
