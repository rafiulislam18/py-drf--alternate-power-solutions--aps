from django.urls import path
from .views import *
from .staff_views import StaffQuoteDetailView, StaffQuoteListView

urlpatterns = [
    path('', QuoteRequestAPIView.as_view(), name='quote-request'),
    # Staff dashboard (read-only): every quote request, website form + container.
    path('staff/', StaffQuoteListView.as_view(), name='staff-quote-list'),
    path('staff/<str:kind>/<int:pk>/', StaffQuoteDetailView.as_view(), name='staff-quote-detail'),
]
