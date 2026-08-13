import logging
import random
import string
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from .authentication import GasGuardJWTAuthentication
from .emails import CODE_TTL_MINUTES, send_verification_email
from .models import GasGuardUser, PendingRegistration
from .serializers import (
    EmailTokenObtainPairSerializer,
    NotificationPrefsSerializer,
    RegisterSerializer,
    ResendVerificationSerializer,
    UserSerializer,
    VerifyEmailSerializer,
)
from .tokens import GasGuardRefreshToken

logger = logging.getLogger(__name__)

User = GasGuardUser


def _new_code():
    """A fresh 6-digit numeric verification code."""
    return ''.join(random.choices(string.digits, k=6))


def _issue_tokens(user):
    """Access/refresh pair plus the serialized user — the login payload."""
    refresh = GasGuardRefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': UserSerializer(user).data,
    }


class RegisterView(APIView):
    """
    POST: begin a self-service sign-up.

    We don't create the account yet — we stash a PendingRegistration (with the
    password already hashed) and email a 6-digit code. The account is created
    only once the code is confirmed at ``verify-email/``. New accounts start on
    the device-only tier; subscribing (PayFast) is a separate step.
    """

    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': 'Invalid input.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = serializer.validated_data['email']
        code = _new_code()
        expires_at = timezone.now() + timedelta(minutes=CODE_TTL_MINUTES)

        # One live pending row per email — clear any earlier attempt.
        PendingRegistration.objects.filter(email__iexact=email).delete()
        pending = PendingRegistration.objects.create(
            first_name=serializer.validated_data.get('first_name', ''),
            last_name=serializer.validated_data.get('last_name', ''),
            email=email,
            # Store the hash, never the plaintext password.
            password=make_password(serializer.validated_data['password']),
            verification_code=code,
            verification_code_expires_at=expires_at,
        )

        if not send_verification_email(email, code, pending.first_name):
            # No point keeping a pending row the user can never verify.
            logger.error(f'Verification email failed during registration for {email}')
            pending.delete()
            return Response(
                {'detail': 'Could not send the verification email. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(f'Pending registration created for {email}')
        return Response(
            {
                'detail': 'Verification code sent. Check your inbox to activate your account.',
                'email': email,
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(APIView):
    """
    POST: confirm a registration code and create the account.

    On success the real User is created (device tier), the pending row is
    removed, and a JWT pair is returned so the user is signed straight in.
    """

    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': 'Invalid input.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = serializer.validated_data['email']
        code = serializer.validated_data['code']

        try:
            pending = PendingRegistration.objects.get(
                email__iexact=email, verification_code=code
            )
        except PendingRegistration.DoesNotExist:
            return Response(
                {'detail': 'Invalid verification code or email address.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if pending.is_expired:
            pending.delete()
            return Response(
                {'detail': 'This code has expired. Please register again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Guard the race where the email got verified by a parallel request.
        if User.objects.filter(email__iexact=email).exists():
            PendingRegistration.objects.filter(email__iexact=email).delete()
            return Response(
                {'detail': 'This email is already registered. Please sign in.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build the user directly so the already-hashed password is preserved
        # (create_user would re-hash a plaintext value).
        user = User(
            email=email,
            first_name=pending.first_name,
            last_name=pending.last_name,
            is_active=True,
        )
        user.password = pending.password
        user.save()

        PendingRegistration.objects.filter(email__iexact=email).delete()
        logger.info(f'Account activated via email verification: {email}')

        return Response(
            {'detail': 'Email verified. Your account is now active.', **_issue_tokens(user)},
            status=status.HTTP_200_OK,
        )


class ResendVerificationView(APIView):
    """POST: re-issue a verification code for an outstanding registration."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResendVerificationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': 'Invalid input.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = serializer.validated_data['email']

        # Collapse any duplicate pending rows to a single canonical one.
        pending_qs = PendingRegistration.objects.filter(email__iexact=email)
        if not pending_qs.exists():
            logger.warning(
                f'Resend verification requested for {email} with no pending registration'
            )
            return Response(
                {'detail': 'No pending registration found for this email. Please register first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        pending = pending_qs.order_by('-created_at').first()
        pending_qs.exclude(pk=pending.pk).delete()

        pending.verification_code = _new_code()
        pending.verification_code_expires_at = timezone.now() + timedelta(
            minutes=CODE_TTL_MINUTES
        )
        pending.save(
            update_fields=['verification_code', 'verification_code_expires_at']
        )

        if not send_verification_email(
            email, pending.verification_code, pending.first_name
        ):
            logger.error(f'Resend verification email failed for {email}')
            return Response(
                {'detail': 'Could not send the verification email. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(f'Verification code resent for {email}')
        return Response(
            {'detail': 'A new verification code has been sent to your email.'},
            status=status.HTTP_200_OK,
        )


class LoginView(APIView):
    """POST: obtain an access/refresh pair from email + password."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = EmailTokenObtainPairSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """POST: blacklist the supplied refresh token so it can't be reused."""

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response(
                {'detail': 'A refresh token is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            GasGuardRefreshToken(refresh_token).blacklist()
        except TokenError:
            return Response(
                {'detail': 'Invalid or expired refresh token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        logger.info(f"User logged out: {request.user}")
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MeView(APIView):
    """GET: return the currently authenticated user's profile."""

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class NotificationPrefsView(APIView):
    """PATCH: update the current user's low-gas email alert preferences.

    Subscriber-only — email alerts are a paid feature, so the tier is
    enforced here as well as hidden in the UI.
    """

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        if request.user.tier != request.user.Tier.SUBSCRIBED:
            return Response(
                {'detail': 'Email alerts require a subscription.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = NotificationPrefsSerializer(
            request.user, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            logger.info(f"Notification prefs updated: {request.user.email}")
            return Response(UserSerializer(request.user).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
