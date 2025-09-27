from django.urls import path
from .views import *

urlpatterns = [
    path('', ServiceProjectListAPIView.as_view(), name='service-project-list'),
    path('service/<int:service_id>', ServiceDetailsAPIView.as_view(), name='service-detail'),
    path('project/<int:project_id>', ProjectDetailsAPIView.as_view(), name='project-detail'),
    path('services/', ServiceListAPIView.as_view(), name='service-list'),
]
