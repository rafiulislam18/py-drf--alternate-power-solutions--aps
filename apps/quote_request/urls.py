from django.urls import path
from .views import *

urlpatterns = [
    path('', QuoteRequestAPIView.as_view(), name='quote-request'),
]
