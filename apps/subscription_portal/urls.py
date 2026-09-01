from django.urls import path

from . import views

urlpatterns = [
    path('request-otp/', views.request_otp, name='portal-request-otp'),
    path('verify-otp/', views.verify_otp, name='portal-verify-otp'),
    path('subscriptions/', views.subscriptions, name='portal-subscriptions'),
    path('payments/', views.payments, name='portal-payments'),
    path('cancel/', views.cancel, name='portal-cancel'),
]
