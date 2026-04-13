# urls.py
from django.urls import path
from .views import ServiceRequestCreateAPIView, ContainerProjectListView, ContainerProjectDetailView

urlpatterns = [
    path("service-requests/create/", ServiceRequestCreateAPIView.as_view(), name="service_request_create"),
    path("projects/", ContainerProjectListView.as_view(), name="container_projects_list"),
    path("projects/<int:id>/", ContainerProjectDetailView.as_view(), name="container_project_detail"),
]
