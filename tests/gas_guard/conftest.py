"""
Shared fixtures for the Gas Guard test suite.

Gas Guard authenticates its own ``GasGuardUser`` table via
``GasGuardJWTAuthentication`` (not the project auth user), so we can't use
DRF's ``force_authenticate`` cleanly — the view's own auth class still runs.
Instead we mint a real ``GasGuardRefreshToken`` and send its access token as a
Bearer header, exercising the real auth path end to end.
"""

import pytest
from rest_framework.test import APIClient

from apps.gas_guard.users.models import GasGuardUser
from apps.gas_guard.users.tokens import GasGuardRefreshToken


@pytest.fixture
def gg_user(db):
    """A device-tier Gas Guard user with a known password."""
    user = GasGuardUser(email='member@example.com', first_name='Thabo', last_name='M')
    user.set_password('str0ng-pass-123')
    user.save()
    return user


@pytest.fixture
def gg_subscriber(db):
    """A subscribed-tier Gas Guard user."""
    user = GasGuardUser(
        email='subscriber@example.com',
        first_name='Jane',
        tier=GasGuardUser.Tier.SUBSCRIBED,
    )
    user.set_password('str0ng-pass-123')
    user.save()
    return user


def auth_client_for(user):
    """An APIClient carrying a valid Gas Guard bearer token for ``user``."""
    client = APIClient()
    token = GasGuardRefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


@pytest.fixture
def gg_client(gg_user):
    return auth_client_for(gg_user)


@pytest.fixture
def gg_sub_client(gg_subscriber):
    return auth_client_for(gg_subscriber)
