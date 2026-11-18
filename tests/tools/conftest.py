"""Shared set-up for the tool booking tests. Tests skip migrations, so seed the tool types."""

import importlib
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.core.models import ClientProfile
from apps.staff.models import StaffAccess
from apps.tools.models import Tool, ToolType

from ..client_portal.conftest import api_for

TOOLS = '/tools/'
BOOKINGS = '/tools/bookings/'


@pytest.fixture(autouse=True)
def tool_types(db):
    from django.apps import apps
    importlib.import_module('apps.tools.migrations.0002_seed_types').seed(apps, None)


def make_staff(username, perms=None, first_name='', email=''):
    user = User.objects.create_user(username, password='x', first_name=first_name or username.title(), email=email)
    ClientProfile.objects.create(user=user, role='admin')
    if perms is not None:
        StaffAccess.objects.create(user=user, permissions=perms)
    return user


@pytest.fixture
def boss(db):
    user = User.objects.create_user('boss', password='x', is_staff=True, is_superuser=True, first_name='Boss')
    ClientProfile.objects.create(user=user, role='admin')
    return user


@pytest.fixture
def api(boss):
    return api_for(boss)


@pytest.fixture
def lucky(db):
    return make_staff('lucky', perms=[], first_name='Lucky', email='lucky@aps.example')


def type_id(name='Drills'):
    return ToolType.objects.get(name=name).pk


def add_tool(api, **data):
    data = {'name': 'Hammer drill', 'type': type_id(), 'brand': 'Makita', **data}
    res = api.post(TOOLS, data, format='json')
    assert res.status_code == 201, res.data
    return res.data


def book(api, technician, tools, expect=201, **data):
    """tools: [tool id] or [(tool id, quantity)]."""
    items = [{'tool': t, 'quantity': 1} if isinstance(t, int) else {'tool': t[0], 'quantity': t[1]} for t in tools]
    payload = {'technician': technician.pk, 'items': items, 'date_out': str(timezone.localdate()),
               'notify': False, **data}
    res = api.post(BOOKINGS, payload, format='json')
    assert res.status_code == expect, res.data
    return res.data


def give_back(api, booking, items=None, expect=200, **data):
    """items: None = everything still out, or [item id] / [{item, ...}]."""
    if items is None:
        items = [i['id'] for i in booking['items'] if i['out']]
    lines = [{'item': i} if isinstance(i, int) else i for i in items]
    payload = {'items': lines, 'date': str(timezone.localdate()), 'notify': False, **data}
    res = api.post(f"{BOOKINGS}{booking['id']}/return/", payload, format='json')
    assert res.status_code == expect, res.data
    return res.data


def days_ago(n):
    return str(timezone.localdate() - timedelta(days=n))


def tool_obj(data):
    return Tool.objects.get(pk=data['id'])
