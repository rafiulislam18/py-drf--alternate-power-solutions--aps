from django.urls import path
from .views import (
    CreatePayFastCheckoutSession,
    payfast_notify,
    payfast_return,
    payfast_cancel,
)

urlpatterns = [
    # PayFast endpoints
    path('create-payfast-checkout/', CreatePayFastCheckoutSession.as_view(), name='create_payfast_checkout'),
    path('payfast-notify/', payfast_notify, name='payfast_notify'),  # ITN endpoint
    path('payfast-return/', payfast_return, name='payfast_return'),
    path('payfast-cancel/', payfast_cancel, name='payfast_cancel'),
]
