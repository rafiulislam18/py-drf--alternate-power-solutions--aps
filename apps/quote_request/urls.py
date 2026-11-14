from django.urls import path
from .views import *
from .staff_views import (
    StaffQuoteArchiveView, StaffQuoteBulkArchiveView, StaffQuoteDetailView, StaffQuoteListView, StaffQuoteUnarchiveView,
)

urlpatterns = [
    path('', QuoteRequestAPIView.as_view(), name='quote-request'),
    # Staff dashboard (read-only): every quote request, website form + container.
    path('staff/', StaffQuoteListView.as_view(), name='staff-quote-list'),
    path('staff/bulk-archive/', StaffQuoteBulkArchiveView.as_view(), name='staff-quote-bulk-archive'),
    path('staff/<str:kind>/<int:pk>/', StaffQuoteDetailView.as_view(), name='staff-quote-detail'),
    path('staff/<str:kind>/<int:pk>/archive/', StaffQuoteArchiveView.as_view(), name='staff-quote-archive'),
    path('staff/<str:kind>/<int:pk>/unarchive/', StaffQuoteUnarchiveView.as_view(), name='staff-quote-unarchive'),
]
