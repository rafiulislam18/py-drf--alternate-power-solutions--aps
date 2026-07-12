from django.urls import path

from .views import FaultAlertPickupView

urlpatterns = [
    path('alerts/<uuid:uuid>/pickup/', FaultAlertPickupView.as_view(), name='fault-alert-pickup'),
]
