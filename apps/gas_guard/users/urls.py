from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    ChangePasswordConfirmView,
    ChangePasswordRequestView,
    ForgotPasswordConfirmView,
    ForgotPasswordRequestView,
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

    # Password flows — both are two-step and email-verified: request stashes the
    # new (hashed) password and mails a 6-digit code, confirm applies it.
    # "forgot" is for signed-out users; "change" is for signed-in ones.
    path(
        'forgot-password/',
        ForgotPasswordRequestView.as_view(),
        name='user-forgot-password',
    ),
    path(
        'forgot-password/confirm/',
        ForgotPasswordConfirmView.as_view(),
        name='user-forgot-password-confirm',
    ),
    path(
        'change-password/',
        ChangePasswordRequestView.as_view(),
        name='user-change-password',
    ),
    path(
        'change-password/confirm/',
        ChangePasswordConfirmView.as_view(),
        name='user-change-password-confirm',
    ),
]
