"""
JWT authentication for Gas Guard users.

The project-wide DRF default (``rest_framework_simplejwt.authentication.
JWTAuthentication``) resolves tokens against Django's default ``auth.User`` —
the APS website's user. Gas Guard keeps a *separate* user table, so its views
opt out of that default by setting ``authentication_classes =
[GasGuardJWTAuthentication]`` and this class resolves the token to a
``GasGuardUser`` instead.

It only accepts tokens minted by ``GasGuardRefreshToken`` (they carry the
``gas_guard`` audience marker and a ``gg_user_id`` claim), so a website token can
never authenticate a Gas Guard request and vice-versa.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken

from .models import GasGuardUser
from .tokens import GAS_GUARD_AUDIENCE, GAS_GUARD_USER_ID_CLAIM


class GasGuardJWTAuthentication(JWTAuthentication):
    """Authenticate a request as a ``GasGuardUser`` from a Gas Guard JWT."""

    def get_user(self, validated_token):
        # Reject anything that isn't a Gas Guard token (e.g. a website token
        # that happens to be signed with the same key).
        if validated_token.get('aud') != GAS_GUARD_AUDIENCE:
            raise InvalidToken('Token is not a Gas Guard token.')

        user_id = validated_token.get(GAS_GUARD_USER_ID_CLAIM)
        if user_id is None:
            raise InvalidToken('Token contained no recognizable Gas Guard user id.')

        try:
            user = GasGuardUser.objects.get(pk=user_id)
        except GasGuardUser.DoesNotExist:
            raise AuthenticationFailed('User not found.', code='user_not_found')

        if not user.is_active:
            raise AuthenticationFailed('User is inactive.', code='user_inactive')

        return user
