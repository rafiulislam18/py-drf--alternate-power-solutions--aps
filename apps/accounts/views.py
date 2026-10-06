"""
Dashboard accounts API (mounted at ``/accounts/``). Sign-in itself is
``/dashboard/auth/token/`` (username or email + password).

Public (throttled; replies never reveal whether an email has an account):
- ``POST register/``              {name, email, phone?, password, confirm_password}
  → creates a client account and emails a confirmation link. It can't sign in
  until the email is confirmed. An email that already has an account gets a
  "you already have an account" email instead, with the same reply.
- ``POST verify-email/``          {token} → confirms the address in the link.
- ``POST resend-verification/``   {email} → sends a fresh confirmation link.
- ``POST password/forgot/``       {email} → a reset link to an account's email;
  or, for someone who pays an APS subscription under that email but has never
  had a login, a link to set up their account.
- ``POST password/reset/``        {token, password, confirm_password} → sets the
  password (or creates the subscriber's account), confirms the email, and ends
  every other session. Replies with the email to sign in with.

Signed in:
- ``GET/PATCH me/``               → account details; clients may edit
  company_name and phone.
- ``POST password/change/``       {current_password, password, confirm_password}
  → new password; other sessions end, this one gets fresh tokens.
- ``POST email/change/``          {email, current_password} → emails a
  confirmation link to the new address; it replaces the old one once confirmed.
  ``DELETE email/change/`` drops a pending change.
"""

import logging
import threading
from functools import partial

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import ClientProfile
from apps.subscription_portal.providers import find_subscriber
from utils.exceptions import custom_exception_handler

from .emails import (
    send_account_exists_email,
    send_claim_account_email,
    send_password_reset_email,
    send_verification_email,
)
from .login import find_user_by_email, identifier_taken
from .serializers import (
    AccountSerializer,
    AccountUpdateSerializer,
    ChangeEmailSerializer,
    ChangePasswordSerializer,
    EmailSerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
    TokenSerializer,
    check_password_strength,
    generate_username,
)
from .throttling import AccountsEmailThrottle, AccountsIpThrottle, AccountsPasswordThrottle
from .tokens import password_version, read_reset_token, read_verify_token, reset_state_matches

logger = logging.getLogger('apps.accounts')

_CHECK_INBOX = 'Check your inbox for a link to confirm your email. It can take a minute to arrive.'
_FORGOT_SENT = 'If that email has an APS account or subscription, we\'ve sent it a link to set a new password.'
_RESEND_SENT = 'If that email is waiting to be confirmed, we\'ve sent a new link.'
_BAD_LINK = 'This link has expired or has already been used. Please request a new one.'

# One confirmation email per account + address per this many seconds, however
# often someone presses "resend" or tries to sign in unverified.
VERIFY_RESEND_COOLDOWN = 120


def _run_in_background(fn, *args):
    """Send off the request thread: waiting on SMTP only for real accounts
    would let the response time reveal which emails exist."""
    thread = threading.Thread(target=fn, args=args, daemon=True)
    thread.start()
    return thread


def issue_tokens(user):
    """A refresh/access pair for ``user`` carrying the password version."""
    refresh = RefreshToken.for_user(user)
    refresh['pv'] = password_version(user)
    return {'refresh': str(refresh), 'access': str(refresh.access_token)}


def queue_verification(user, email=None):
    """Email a confirmation link unless one went to this address very recently."""
    # Sent to the address exactly as stored (the part before @ may be
    # case-sensitive); the cooldown key ignores case.
    email = (email or user.email or '').strip()
    if not email:
        return False
    if not cache.add(f'accounts:verify-sent:{user.pk}:{email.lower()}', 1, VERIFY_RESEND_COOLDOWN):
        return False
    _run_in_background(send_verification_email, user, email)
    return True


def _profile_for(user):
    try:
        return user.client_profile
    except ClientProfile.DoesNotExist:
        return None


class _AccountsView(generics.GenericAPIView):
    def get_exception_handler(self):
        # Keep field names so the forms can mark the right input.
        return partial(custom_exception_handler, keep_fields=True)


class _PublicView(_AccountsView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AccountsIpThrottle]


class RegisterView(_PublicView):
    serializer_class = RegisterSerializer
    throttle_classes = [AccountsIpThrottle, AccountsEmailThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data['email']

        if identifier_taken(email):
            existing = find_user_by_email(email)
            if existing is not None:
                _run_in_background(send_account_exists_email, existing)
            return Response({'detail': _CHECK_INBOX, 'email': email}, status=status.HTTP_201_CREATED)

        with transaction.atomic():
            user = User.objects.create_user(username=generate_username(email), email=email,
                                            password=data['password'])
            ClientProfile.objects.create(user=user, role='client', company_name=data['name'],
                                         phone=data.get('phone', ''), self_registered=True)
        logger.info(f'New self-registered dashboard account {user.username} ({email})')
        queue_verification(user)
        return Response({'detail': _CHECK_INBOX, 'email': email}, status=status.HTTP_201_CREATED)


class VerifyEmailView(_PublicView):
    serializer_class = TokenSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parsed = read_verify_token(serializer.validated_data['token'])
        user = User.objects.filter(pk=parsed[0]).select_related('client_profile').first() if parsed else None
        profile = _profile_for(user) if user else None
        if not user or not user.is_active or not profile:
            return Response({'detail': _BAD_LINK}, status=status.HTTP_400_BAD_REQUEST)
        email = parsed[1]

        with transaction.atomic():
            if email == (user.email or '').lower():
                profile.verified_email = user.email
                profile.save(update_fields=['verified_email'])
            elif profile.pending_email and email == profile.pending_email.lower():
                if identifier_taken(email, exclude_user=user):
                    return Response({'detail': 'Another account now uses that email, so it can\'t be moved to yours.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                user.email = profile.pending_email
                user.save(update_fields=['email'])
                profile.verified_email = profile.pending_email
                profile.pending_email = ''
                profile.save(update_fields=['verified_email', 'pending_email'])
                logger.info(f'Dashboard account {user.username} changed email to {user.email}')
            else:
                return Response({'detail': _BAD_LINK}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': 'Your email is confirmed. You can sign in with it now.', 'email': user.email})


class ResendVerificationView(_PublicView):
    serializer_class = EmailSerializer
    throttle_classes = [AccountsIpThrottle, AccountsEmailThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        user = find_user_by_email(email)
        profile = _profile_for(user) if user else None
        if user and user.is_active and profile and not profile.email_verified:
            queue_verification(user)
        else:
            pending = ClientProfile.objects.filter(pending_email__iexact=email).select_related('user')
            for p in pending[:1]:
                queue_verification(p.user, p.pending_email)
        return Response({'detail': _RESEND_SENT})


class ForgotPasswordView(_PublicView):
    serializer_class = EmailSerializer
    throttle_classes = [AccountsIpThrottle, AccountsEmailThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        user = find_user_by_email(email)
        if user is not None:
            if user.is_active:
                _run_in_background(send_password_reset_email, user)
        elif not identifier_taken(email) and find_subscriber(email):
            _run_in_background(send_claim_account_email, email)
        return Response({'detail': _FORGOT_SENT})


class ResetPasswordView(_PublicView):
    serializer_class = ResetPasswordSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        parsed = read_reset_token(data['token'])
        if not parsed:
            return Response({'detail': _BAD_LINK}, status=status.HTTP_400_BAD_REQUEST)
        kind, ident, state = parsed

        if kind == 'reset':
            user = User.objects.filter(pk=ident, is_active=True).first()
            if not user or not reset_state_matches(user, state):
                return Response({'detail': _BAD_LINK}, status=status.HTTP_400_BAD_REQUEST)
            check_password_strength(data['password'], user)
            with transaction.atomic():
                user.set_password(data['password'])
                user.save(update_fields=['password'])
                profile = _profile_for(user)
                if profile and user.email:
                    # The link went to this address, so they own it.
                    profile.verified_email = user.email
                    profile.save(update_fields=['verified_email'])
            logger.info(f'Dashboard account {user.username} reset its password')
            return Response({'detail': 'Your new password is set.', 'login': user.email or user.username})

        # kind == 'claim': an existing subscriber setting up their first login.
        email = ident
        subscriber = find_subscriber(email)
        if identifier_taken(email):
            return Response({'detail': 'You already have an account with this email. Sign in, or use '
                                       '"Forgot password" again for a fresh link.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not subscriber:
            return Response({'detail': _BAD_LINK}, status=status.HTTP_400_BAD_REQUEST)
        check_password_strength(data['password'], User(username=email.split('@')[0], email=email))
        with transaction.atomic():
            user = User.objects.create_user(username=generate_username(email), email=email,
                                            password=data['password'])
            ClientProfile.objects.create(
                user=user, role='client', company_name=subscriber['name'][:200] or email.split('@')[0],
                phone=subscriber['phone'][:30], verified_email=email, self_registered=True,
            )
        logger.info(f'Subscriber {email} set up dashboard account {user.username}')
        return Response({'detail': 'Your account is ready.', 'login': email}, status=status.HTTP_201_CREATED)


class MeView(_AccountsView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(AccountSerializer(request.user).data)

    def patch(self, request):
        profile = _profile_for(request.user)
        if profile is None:
            raise PermissionDenied('Staff account details are managed in the admin.')
        serializer = AccountUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(profile, field, value)
        profile.save()
        return Response(AccountSerializer(request.user).data)


class ChangePasswordView(_AccountsView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AccountsPasswordThrottle]
    serializer_class = ChangePasswordSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data['password'])
        user.save(update_fields=['password'])
        logger.info(f'Dashboard account {user.username} changed its password')
        # The password version changed, so every other session is now dead;
        # hand this one a fresh pair so it carries on.
        return Response({'detail': 'Your password has been changed.', **issue_tokens(user)})


class ChangeEmailView(_AccountsView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AccountsPasswordThrottle]
    serializer_class = ChangeEmailSerializer

    def post(self, request):
        profile = _profile_for(request.user)
        if profile is None:
            raise PermissionDenied('Staff emails are managed in the admin.')
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        user = request.user

        if email == (user.email or '').lower():
            if profile.email_verified:
                return Response({'email': ['That\'s already your email.']}, status=status.HTTP_400_BAD_REQUEST)
            # Re-confirming the current (unverified) address.
            profile.pending_email = ''
            profile.save(update_fields=['pending_email'])
            cache.delete(f'accounts:verify-sent:{user.pk}:{email}')
            queue_verification(user)
        else:
            profile.pending_email = email
            profile.save(update_fields=['pending_email'])
            cache.delete(f'accounts:verify-sent:{user.pk}:{email}')
            queue_verification(user, email)
        return Response({
            'detail': f'We\'ve sent a confirmation link to {email}. Your email changes once you click it.',
            **AccountSerializer(user).data,
        })

    def delete(self, request):
        profile = _profile_for(request.user)
        if profile is not None and profile.pending_email:
            profile.pending_email = ''
            profile.save(update_fields=['pending_email'])
        return Response(AccountSerializer(request.user).data)
