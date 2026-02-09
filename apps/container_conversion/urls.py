# urls.py
from django.urls import path
from .views import ServiceRequestCreateAPIView

urlpatterns = [
    path("service-requests/create/", ServiceRequestCreateAPIView.as_view(), name="service_request_create"),
]
