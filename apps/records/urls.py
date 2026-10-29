from django.urls import path

from . import views

urlpatterns = [
    path('trash/', views.TrashListView.as_view(), name='records-trash'),
    path('trash/<str:type_key>/<str:pk>/', views.TrashPreviewView.as_view(), name='records-trash-preview'),
    path('trash/<str:type_key>/<str:pk>/restore/', views.TrashRestoreView.as_view(), name='records-trash-restore'),
    path('audit/', views.AuditLogView.as_view(), name='records-audit'),
    path('history/<str:type_key>/<str:pk>/', views.ItemHistoryView.as_view(), name='records-history'),
]
