from django.urls import path

from . import views

urlpatterns = [
    path('register/', views.RegisterView.as_view(), name='accounts-register'),
    path('verify-email/', views.VerifyEmailView.as_view(), name='accounts-verify-email'),
    path('resend-verification/', views.ResendVerificationView.as_view(), name='accounts-resend-verification'),
    path('password/forgot/', views.ForgotPasswordView.as_view(), name='accounts-password-forgot'),
    path('password/reset/', views.ResetPasswordView.as_view(), name='accounts-password-reset'),
    path('password/change/', views.ChangePasswordView.as_view(), name='accounts-password-change'),
    path('email/change/', views.ChangeEmailView.as_view(), name='accounts-email-change'),
    path('me/', views.MeView.as_view(), name='accounts-me'),
]
