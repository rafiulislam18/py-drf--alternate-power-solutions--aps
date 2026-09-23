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
from .emails import (
    CODE_TTL_MINUTES,
    send_password_reset_email,
    send_verification_email,
)
from .models import GasGuardUser, PasswordResetCode, PendingRegistration
from .serializers import (
    ChangePasswordRequestSerializer,
    EmailTokenObtainPairSerializer,
    ForgotPasswordRequestSerializer,
    NotificationPrefsSerializer,
    PasswordCodeConfirmSerializer,
    RegisterSerializer,
    ResendVerificationSerializer,
    UserSerializer,
    VerifyEmailSerializer,
)
from .throttling import CodeSendThrottle, CodeVerifyThrottle
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

    Throttled to 5/min per email, like every other code-sending endpoint.
    """

    permission_classes = [AllowAny]
    throttle_classes = [CodeSendThrottle]

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

    Throttled to 10/min per email so codes can't be guessed in bulk.
    """

    permission_classes = [AllowAny]
    throttle_classes = [CodeVerifyThrottle]

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
    """POST: re-issue a verification code for an outstanding registration.

    Throttled to 5/min per email — this endpoint exists to be called repeatedly,
    so it is the easiest one to abuse for email bombing.
    """

    permission_classes = [AllowAny]
    throttle_classes = [CodeSendThrottle]

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


def _start_password_flow(email, new_password, purpose, first_name=''):
    """
    Stash a pending password change and email its confirmation code.

    Shared by both flows — they differ only in how the account is identified
    (posted email vs the JWT) and in the email copy. Returns True if the code
    was sent; False means the email failed and the pending row was rolled back,
    so the caller can decide whether to surface that (change) or stay silent
    (forgot, where revealing anything would leak account existence).
    """
    code = _new_code()
    expires_at = timezone.now() + timedelta(minutes=CODE_TTL_MINUTES)

    # One live row per (email, purpose) — a new request invalidates the old code.
    PasswordResetCode.objects.filter(email__iexact=email, purpose=purpose).delete()
    pending = PasswordResetCode.objects.create(
        email=email,
        purpose=purpose,
        new_password=make_password(new_password),
        verification_code=code,
        verification_code_expires_at=expires_at,
    )

    is_change = purpose == PasswordResetCode.Purpose.CHANGE
    if not send_password_reset_email(email, code, first_name, is_change=is_change):
        logger.error(f'Password reset email failed for {email} ({purpose})')
        pending.delete()
        return False

    logger.info(f'Password {purpose} code issued for {email}')
    return True


class ForgotPasswordRequestView(APIView):
    """
    POST: start a password reset from the login screen.

    Takes the email and the desired new password, and emails a 6-digit code.
    Nothing changes until that code is confirmed at
    ``forgot-password/confirm/``.

    Always answers 200 with the same message whether or not the email is
    registered — otherwise this endpoint would tell an attacker which addresses
    have accounts. Throttled to 5/min per email.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CodeSendThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = ForgotPasswordRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': 'Invalid input.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = serializer.validated_data['email']
        generic = Response(
            {
                'detail': 'If that email has a Gas Guard account, a verification '
                          'code is on its way.',
                'email': email,
            }
        )

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            # Silent no-op: same response, no email, no row.
            logger.info(f'Password reset requested for unknown email {email}')
            return generic

        _start_password_flow(
            user.email,
            serializer.validated_data['new_password'],
            PasswordResetCode.Purpose.FORGOT,
            user.first_name,
        )
        # A send failure is deliberately not surfaced here — reporting it would
        # distinguish a real account from an unknown one.
        return generic


class ChangePasswordRequestView(APIView):
    """
    POST: start a password change for the signed-in user.

    The account comes from the JWT, so only the new password is posted. A
    6-digit code goes to the account's login address; nothing changes until it
    is confirmed at ``change-password/confirm/``. Throttled to 5/min.
    """

    authentication_classes = [GasGuardJWTAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [CodeSendThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = ChangePasswordRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': 'Invalid input.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user
        sent = _start_password_flow(
            user.email,
            serializer.validated_data['new_password'],
            PasswordResetCode.Purpose.CHANGE,
            user.first_name,
        )
        if not sent:
            return Response(
                {'detail': 'Could not send the verification email. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            'detail': 'Verification code sent. Check your inbox to confirm the change.',
            'email': user.email,
        })


def _blacklist_all_refresh_tokens(user):
    """
    Invalidate every outstanding refresh token for a Gas Guard user.

    simplejwt's OutstandingToken rows are keyed to the project's ``auth.User``,
    not GasGuardUser, so they can't be looked up by user here — Gas Guard tokens
    carry their owner in a custom claim instead. We match on that claim and
    blacklist what we find. Best-effort: a failure here must not block a
    password change that has already been applied.
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )
        from .tokens import GAS_GUARD_USER_ID_CLAIM

        for token in OutstandingToken.objects.all():
            try:
                parsed = GasGuardRefreshToken(token.token)
            except TokenError:
                continue  # already expired — nothing to blacklist
            if parsed.get(GAS_GUARD_USER_ID_CLAIM) == user.id:
                BlacklistedToken.objects.get_or_create(token=token)
    except Exception as e:  # pragma: no cover — never block the password change
        logger.error(f'Could not blacklist refresh tokens for {user.email}: {e}')


def _confirm_password_change(request, purpose):
    """
    Apply a pending password change once its code checks out.

    Shared by both confirm endpoints. Wrong codes are counted against the row
    and burn it after ``MAX_ATTEMPTS``, so a code can't be guessed within its
    15-minute life. On success the password is set and every outstanding
    refresh token is blacklisted — a password change should end other sessions.
    """
    serializer = PasswordCodeConfirmSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {'detail': 'Invalid input.', 'errors': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    email = serializer.validated_data['email']
    code = serializer.validated_data['code']
    invalid = Response(
        {'detail': 'That code is invalid or has expired. Please request a new one.'},
        status=status.HTTP_400_BAD_REQUEST,
    )

    pending = PasswordResetCode.objects.filter(
        email__iexact=email, purpose=purpose
    ).first()
    if pending is None:
        return invalid

    if pending.is_expired:
        pending.delete()
        return invalid

    if pending.verification_code != code:
        pending.attempts += 1
        if pending.attempts >= PasswordResetCode.MAX_ATTEMPTS:
            logger.warning(f'Password code burned after too many attempts for {email}')
            pending.delete()
        else:
            pending.save(update_fields=['attempts'])
        return invalid

    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user is None:
        pending.delete()
        return invalid

    # The hash was computed at request time; move it across verbatim.
    user.password = pending.new_password
    user.save(update_fields=['password'])
    pending.delete()

    # A password change ends other sessions: blacklist every outstanding
    # refresh token for this user so old devices must sign in again.
    _blacklist_all_refresh_tokens(user)

    logger.info(f'Password changed ({purpose}) for {user.email}')
    return Response({'detail': 'Your password has been updated. Please sign in.'})


class ForgotPasswordConfirmView(APIView):
    """POST: confirm a forgotten-password reset with its emailed code."""

    # No authentication at all: the project-wide default (JWTAuthentication)
    # resolves against the website's auth.User and 401s on a Gas Guard token
    # before permissions are even consulted. The emailed code is the proof here.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CodeVerifyThrottle]

    @transaction.atomic
    def post(self, request):
        return _confirm_password_change(request, PasswordResetCode.Purpose.FORGOT)


class ChangePasswordConfirmView(APIView):
    """
    POST: confirm a signed-in password change with its emailed code.

    Left open to unauthenticated callers on purpose: the change blacklists the
    caller's own tokens, and a user whose access token expires mid-flow should
    still be able to finish with the code they were sent. The code itself is
    the proof.
    """

    # See ForgotPasswordConfirmView — the default authenticator would 401 a Gas
    # Guard token before AllowAny is reached.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CodeVerifyThrottle]

    @transaction.atomic
    def post(self, request):
        return _confirm_password_change(request, PasswordResetCode.Purpose.CHANGE)
