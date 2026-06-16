from django.urls import path

from .views import (
    ScaleDeviceListView,
    WeightReadingIngestView,
    WeightReadingListView,
)

urlpatterns = [
    # Device-facing: submit a reading.
    path('readings/', WeightReadingIngestView.as_view(), name='weight-reading-ingest'),
    # Staff-facing: read data.
    path('readings/list/', WeightReadingListView.as_view(), name='weight-reading-list'),
    path('devices/', ScaleDeviceListView.as_view(), name='scale-device-list'),
]
