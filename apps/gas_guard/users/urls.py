from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    LoginView,
    LogoutView,
    MeView,
    NotificationPrefsView,
    RegisterView,
    ResendVerificationView,
    VerifyEmailView,
)

urlpatterns = [
    # Self-service sign-up with email verification: register stashes a pending
    # row + emails a code, verify-email creates the account and logs the user in.
    path('register/', RegisterView.as_view(), name='user-register'),
    path('verify-email/', VerifyEmailView.as_view(), name='user-verify-email'),
    path(
        'resend-verification/',
        ResendVerificationView.as_view(),
        name='user-resend-verification',
    ),
    path('login/', LoginView.as_view(), name='user-login'),
    path('logout/', LogoutView.as_view(), name='user-logout'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('me/', MeView.as_view(), name='user-me'),
    path(
        'me/notifications/',
        NotificationPrefsView.as_view(),
        name='user-notification-prefs',
    ),
]
