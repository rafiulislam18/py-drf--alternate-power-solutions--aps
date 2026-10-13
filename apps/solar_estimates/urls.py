from django.urls import path

from . import views

urlpatterns = [
    path('', views.EstimateListView.as_view(), name='solar-estimate-list'),
    path('preview/', views.PreviewView.as_view(), name='solar-estimate-preview'),
    path('settings/', views.SettingsView.as_view(), name='solar-estimate-settings'),
    path('catalogue/', views.CatalogueCreateView.as_view(), name='solar-estimate-catalogue'),
    path('catalogue/<int:pk>/', views.CatalogueDetailView.as_view(), name='solar-estimate-catalogue-item'),
    path('<int:pk>/', views.EstimateDetailView.as_view(), name='solar-estimate-detail'),
    path('<int:pk>/confirm/', views.EstimateConfirmView.as_view(), name='solar-estimate-confirm'),
    path('<int:pk>/reopen/', views.EstimateReopenView.as_view(), name='solar-estimate-reopen'),
    path('<int:pk>/pdf/', views.EstimatePdfView.as_view(), name='solar-estimate-pdf'),
    path('<int:pk>/roof-image/', views.RoofImageView.as_view(), name='solar-estimate-roof-image'),
]
