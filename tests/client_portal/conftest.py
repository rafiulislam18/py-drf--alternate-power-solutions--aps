"""
Shared fixtures for the client dashboard API (apps.client_portal).

A client is one dashboard login per company: an ``auth.User`` with a
``core.ClientProfile`` (role 'client'). Its sites are ``solar_dashboard.Site``
rows. Most tests sign in with ``force_authenticate``; the ones where the auth
path matters get a real token from ``/dashboard/auth/token/``.
"""

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.client_portal.models import Ticket
from apps.core.models import ClientProfile
from apps.services_and_projects.models import Service
from apps.solar_dashboard.models import Site

PASSWORD = 'Sunny-Days-2026'
TOKEN_URL = '/dashboard/auth/token/'


def make_client(username, email='', company_name='', verified=True, phone='', **profile):
    """A client account; ``verified`` marks its email as confirmed."""
    user = User.objects.create_user(username=username, email=email, password=PASSWORD)
    ClientProfile.objects.create(
        user=user, role='client', company_name=company_name, phone=phone,
        verified_email=email if verified else '', **profile,
    )
    return user


def api_for(user=None):
    api = APIClient()
    if user is not None:
        api.force_authenticate(user=user)
    return api


def login(identifier, password=PASSWORD):
    """A real sign-in: returns the token response."""
    return APIClient().post(TOKEN_URL, {'username': identifier, 'password': password}, format='json')


def bearer(access):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
    return api


def make_service(title='Electrical'):
    """One of APS's services (Site content), found or created by title."""
    return Service.objects.get_or_create(
        title=title, defaults={'short_description': f'{title} work', 'long_description': '-', 'image': 'services/x.jpg'},
    )[0]


def make_ticket(client, site, **kw):
    """A ticket; ``service`` is the chosen service's name (plain text)."""
    defaults = {
        'title': 'Something broke',
        'description': 'Details',
        'service': 'Electrical',
    }
    defaults.update(kw)
    return Ticket.objects.create(client=client, site=site, **defaults)


@pytest.fixture(autouse=True)
def _isolate(settings, tmp_path):
    cache.clear()
    settings.MEDIA_ROOT = str(tmp_path)
    yield
    cache.clear()


@pytest.fixture
def client_user(db):
    return make_client('acme', email='sipho@acme.example', company_name='Acme Properties', phone='021 555 0100')


@pytest.fixture
def other_client(db):
    return make_client('otherco', email='ops@other.example', company_name='Other Co')


@pytest.fixture
def site(client_user):
    return Site.objects.create(client=client_user, name='Head office', created_by=client_user)


@pytest.fixture
def warehouse(client_user):
    # Added by APS (no created_by), e.g. while writing a solar report.
    return Site.objects.create(client=client_user, name='Warehouse')


@pytest.fixture
def other_site(other_client):
    return Site.objects.create(client=other_client, name='Their site', created_by=other_client)


@pytest.fixture
def api(client_user):
    return api_for(client_user)
