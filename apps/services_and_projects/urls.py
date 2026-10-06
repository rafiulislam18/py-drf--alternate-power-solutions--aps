from django.urls import path
from .views import *

urlpatterns = [
    path('', ServiceProjectListAPIView.as_view(), name='service-project-list'),
    path('service/<int:service_id>', ServiceDetailsAPIView.as_view(), name='service-detail'),
    path('project/<int:project_id>', ProjectDetailsAPIView.as_view(), name='project-detail'),
    path('services/', ServiceListAPIView.as_view(), name='service-list'),

    # Dashboard "Site content" editor (admin only)
    path('admin/services/', AdminServiceListCreateView.as_view(), name='admin-service-list'),
    path('admin/services/<int:pk>/', AdminServiceDetailView.as_view(), name='admin-service-detail'),
    path('admin/projects/', AdminProjectListCreateView.as_view(), name='admin-project-list'),
    path('admin/projects/<int:pk>/', AdminProjectDetailView.as_view(), name='admin-project-detail'),
]
