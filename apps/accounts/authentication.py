"""
Dashboard JWTs that end when the password changes.

Every dashboard token (access and refresh) carries ``pv`` — a hash of the
user's password (``tokens.password_version``). This authentication class and
the refresh serializer refuse a token whose ``pv`` no longer matches, so
changing or resetting a password signs the account out everywhere else at once.
Tokens without ``pv`` (issued before this existed) are refused too: everyone
signs in once more after the release.
"""

from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from .tokens import password_version

_ENDED = 'Your session has ended. Please sign in again.'


def _pv_matches(user, token):
    pv = token.get('pv')
    return isinstance(pv, str) and pv == password_version(user)


class DashboardJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if not _pv_matches(user, validated_token):
            raise AuthenticationFailed(_ENDED, code='token_not_valid')
        return user


class DashboardTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        refresh = RefreshToken(attrs['refresh'])  # raises TokenError → 401 via the view
        user_id = refresh.get(api_settings.USER_ID_CLAIM)
        user = get_user_model().objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first()
        if user is None or not user.is_active or not _pv_matches(user, refresh):
            raise InvalidToken(_ENDED)
        return super().validate(attrs)
