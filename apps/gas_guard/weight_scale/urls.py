from django.urls import path

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
]
