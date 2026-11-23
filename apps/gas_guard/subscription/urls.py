from django.urls import path

from .views import (
    CancelSubscriptionView,
    CancelSwapAddonView,
    CreatePayFastCheckoutSession,
    CreateSwapAddonCheckoutSession,
    PaymentHistoryView,
    ResubscribeCheckoutSession,
    ResubscribeSwapAddonCheckoutSession,
    SubscriptionStatusView,
    payfast_cancel,
    payfast_notify,
    payfast_return,
)

urlpatterns = [
    path('status/', SubscriptionStatusView.as_view(), name='subscription-status'),
    path('payments/', PaymentHistoryView.as_view(), name='subscription-payments'),
    path('create-payfast-checkout/', CreatePayFastCheckoutSession.as_view(), name='create-payfast-checkout'),
    path('create-swap-addon-checkout/', CreateSwapAddonCheckoutSession.as_view(), name='create-swap-addon-checkout'),
    path('cancel/', CancelSubscriptionView.as_view(), name='cancel-subscription'),
    # A cancelled plan that's still running: new subscription, first charge on its end date.
    path('resubscribe-checkout/', ResubscribeCheckoutSession.as_view(), name='resubscribe-checkout'),
    path('resubscribe-swap-addon-checkout/', ResubscribeSwapAddonCheckoutSession.as_view(),
         name='resubscribe-swap-addon-checkout'),
    path('cancel-swap-addon/', CancelSwapAddonView.as_view(), name='cancel-swap-addon'),
    path('payfast-notify/', payfast_notify, name='payfast-notify'),  # ITN endpoint (both plans)
    path('payfast-return/', payfast_return, name='payfast-return'),
    path('payfast-cancel/', payfast_cancel, name='payfast-cancel'),
]
