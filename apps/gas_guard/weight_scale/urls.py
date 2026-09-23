from django.urls import path

from .admin_views import (
    AdminClientListView,
    AdminSiteDetailView,
    AdminSiteListView,
    AdminSiteReadingsView,
    AdminSiteRegenerateKeyView,
    AdminSiteSubscriptionsView,
)
from .views import (
    FleetConsumptionView,
    ScaleDeviceListView,
    SiteConsumptionView,
    SiteDetailView,
    SiteListView,
    WeightReadingIngestView,
    WeightReadingListView,
)

urlpatterns = [
    # Device-facing: submit a reading.
    path('readings/', WeightReadingIngestView.as_view(), name='weight-reading-ingest'),
    # Staff-facing: read data.
    path('readings/list/', WeightReadingListView.as_view(), name='weight-reading-list'),
    path('devices/', ScaleDeviceListView.as_view(), name='scale-device-list'),
    # Owner-facing: dashboard data.
    path('sites/', SiteListView.as_view(), name='site-list'),
    path(
        'sites/consumption/',
        FleetConsumptionView.as_view(),
        name='fleet-consumption',
    ),
    path('sites/<int:pk>/', SiteDetailView.as_view(), name='site-detail'),
    path(
        'sites/<int:pk>/consumption/',
        SiteConsumptionView.as_view(),
        name='site-consumption',
    ),

    # ── Admin panel (Gas Guard staff only) ────────────────────────────────
    # Backs /gas-guard/admin on the frontend: register sites, read back the
    # API key to flash, assign a site to a client, and check readings.
    path('admin/sites/', AdminSiteListView.as_view(), name='admin-site-list'),
    path(
        'admin/sites/<int:pk>/',
        AdminSiteDetailView.as_view(),
        name='admin-site-detail',
    ),
    path(
        'admin/sites/<int:pk>/regenerate-key/',
        AdminSiteRegenerateKeyView.as_view(),
        name='admin-site-regenerate-key',
    ),
    path(
        'admin/sites/<int:pk>/readings/',
        AdminSiteReadingsView.as_view(),
        name='admin-site-readings',
    ),
    path('admin/clients/', AdminClientListView.as_view(), name='admin-client-list'),
    path(
        'admin/subscriptions/',
        AdminSiteSubscriptionsView.as_view(),
        name='admin-subscriptions',
    ),
]
