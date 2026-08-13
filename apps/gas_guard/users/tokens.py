"""
JWT tokens for Gas Guard users.

Gas Guard is not the project's ``AUTH_USER_MODEL`` (the APS website keeps
``auth.User``), so SimpleJWT's default ``RefreshToken.for_user`` — which keys on
the swappable auth model and the global ``SIMPLE_JWT`` claim names — would issue
a token that the project-wide ``JWTAuthentication`` resolves to the *wrong* user
table. To keep the two auth systems from crossing wires, Gas Guard tokens:

* carry the Gas Guard user id under a dedicated ``gg_user_id`` claim, and
* stamp an ``aud`` (audience) marker so a Gas Guard token is never accepted by
  the website's authenticator and vice-versa.

Pair these with ``GasGuardJWTAuthentication`` (see ``authentication.py``).
"""
from rest_framework_simplejwt.tokens import RefreshToken

# Audience marker distinguishing Gas Guard tokens from the website's tokens.
GAS_GUARD_AUDIENCE = 'gas_guard'
# Claim carrying the GasGuardUser primary key.
GAS_GUARD_USER_ID_CLAIM = 'gg_user_id'


class GasGuardRefreshToken(RefreshToken):
    """A refresh token bound to a ``GasGuardUser`` (not the project auth user)."""

    @classmethod
    def for_user(cls, user):
        # Build a bare token (skips SimpleJWT's default user-id claim, which is
        # keyed on the swappable auth model) and add our own claims.
        token = cls()
        token[GAS_GUARD_USER_ID_CLAIM] = user.pk
        token['aud'] = GAS_GUARD_AUDIENCE
        return token
