from django.urls import path
from .views import (
    CreatePayFastCheckoutSession,
    payfast_notify,
    payfast_return,
    payfast_cancel,
)

urlpatterns = [
    path('create-solar-cleaning-checkout/', CreatePayFastCheckoutSession.as_view(), name='create_solar_cleaning_checkout'),
    path('payfast-notify/', payfast_notify, name='solar_cleaning_payfast_notify'),
    path('payfast-return/', payfast_return, name='solar_cleaning_payfast_return'),
    path('payfast-cancel/', payfast_cancel, name='solar_cleaning_payfast_cancel'),
]