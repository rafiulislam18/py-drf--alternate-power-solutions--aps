from django.urls import path

from . import views

urlpatterns = [
    path('', views.ToolListView.as_view(), name='tools'),
    path('options/', views.ToolOptionsView.as_view(), name='tool-options'),
    path('settings/', views.ToolSettingsView.as_view(), name='tool-settings'),
    path('types/', views.ToolTypeListView.as_view(), name='tool-types'),
    path('types/<int:pk>/', views.ToolTypeDetailView.as_view(), name='tool-type-detail'),
    path('who-has-what/', views.WhoHasWhatView.as_view(), name='tool-who-has-what'),
    path('bookings/', views.BookingListView.as_view(), name='tool-bookings'),
    path('bookings/check/', views.BookingCheckView.as_view(), name='tool-booking-check'),
    path('bookings/bulk-archive/', views.BookingBulkArchiveView.as_view(), name='tool-booking-bulk-archive'),
    path('bookings/<int:pk>/', views.BookingDetailView.as_view(), name='tool-booking-detail'),
    path('bookings/<int:pk>/return/', views.BookingReturnView.as_view(), name='tool-booking-return'),
    path('bookings/<int:pk>/archive/', views.BookingArchiveView.as_view(), name='tool-booking-archive'),
    path('bookings/<int:pk>/unarchive/', views.BookingUnarchiveView.as_view(), name='tool-booking-unarchive'),
    path('<int:pk>/', views.ToolDetailView.as_view(), name='tool-detail'),
]
