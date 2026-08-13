from django.urls import path

from .views import DemoBookingCreateView

urlpatterns = [
    # Public: landing-page "book a demo" enquiry.
    path('demo-bookings/', DemoBookingCreateView.as_view(), name='demo-booking-create'),
]
