from django.urls import path

from . import views

urlpatterns = [
    path('permissions/', views.PermissionCatalogueView.as_view(), name='staff-permissions'),
    path('accounts/', views.StaffAccountListView.as_view(), name='staff-accounts'),
    path('accounts/<int:pk>/', views.StaffAccountDetailView.as_view(), name='staff-account-detail'),
]
