from django.urls import path
from .views import ServiceProjectListAPIView

urlpatterns = [
    path("", ServiceProjectListAPIView.as_view(), name="service-list"),
]
