"""
Tests for the staff dashboard's quote requests (apps.quote_request.staff_views):
staff only, website + solar + container quotes in one newest-first list, the
kind / status / search filters and tab counts, paging, and the detail view.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient

from apps.container_conversion.models import ServiceRequest
from apps.core.models import ClientProfile
from apps.quote_request.models import QuoteRequest, SolarQuoteDetails
from apps.services_and_projects.models import Service

pytestmark = pytest.mark.django_db

LIST = '/quote-request/staff/'


def service(title):
    return Service.objects.get_or_create(
        title=title, defaults={'short_description': '-', 'long_description': '-', 'image': 'services/x.jpg'},
    )[0]


def quote(name, title='Electrical Services', ago=0, **kw):
    q = QuoteRequest.objects.create(name=name, phone='021 555 0100', email=f'{name.lower()}@ex.example',
                                    service=service(title), **kw)
    QuoteRequest.objects.filter(pk=q.pk).update(created_at=timezone.now() - timedelta(hours=ago))
    return q


def container(first, ago=0, **kw):
    c = ServiceRequest.objects.create(
        first_name=first, last_name='Mokoena', email=f'{first.lower()}@ex.example', phone='082 000 0000',
        unit_type='container', intended_use='office', modular_size='6m',
        transport_or_export_address='12 Dock Rd, Cape Town', **kw,
    )
    ServiceRequest.objects.filter(pk=c.pk).update(created_at=timezone.now() - timedelta(hours=ago))
    return c


@pytest.fixture
def staff():
    api = APIClient()
    api.force_authenticate(User.objects.create_user('ops', is_staff=True))
    return api


@pytest.fixture
def data():
    g = quote('Gina', ago=3, company='Urban Growth', message='Need a DB board check')
    s = quote('Sam', 'Solar PV System Design & Installation', ago=1, sent_quote=True)
    SolarQuoteDetails.objects.create(quote_request=s, property_type='residential', monthly_bill=3500,
                                     province='western_cape', budget_range='50-150k', additional_notes='North-facing roof')
    c = container('Thandi', ago=2, ablution_unit=True, air_conditioning=True, additional_details='Two windows please')
    return g, s, c


def test_staff_only(data):
    assert APIClient().get(LIST).status_code == 401
    client = User.objects.create_user('acme')
    ClientProfile.objects.create(user=client, role='client')
    api = APIClient()
    api.force_authenticate(client)
    assert api.get(LIST).status_code == 403
    assert api.get('/quote-request/staff/general/1/').status_code == 403


def test_one_list_newest_first_with_counts(staff, data):
    res = staff.get(LIST).data
    assert [r['key'] for r in res['results']] == [f'solar-{data[1].pk}', f'container-{data[2].pk}', f'general-{data[0].pk}']
    assert res['counts'] == {'general': 1, 'solar': 1, 'container': 1, 'all': 3, 'new': 2}
    sam = res['results'][0]
    assert sam['kind_label'] == 'Solar PV quote' and sam['handled'] is True and sam['status_label'] == 'Quote sent'
    thandi = res['results'][1]
    assert thandi['name'] == 'Thandi Mokoena'
    assert thandi['summary'] == '6 Meter (20ft Container) · Office'


@pytest.mark.parametrize('kind,expected', [('general', 'Gina'), ('solar', 'Sam'), ('container', 'Thandi Mokoena')])
def test_kind_filter(staff, data, kind, expected):
    res = staff.get(LIST, {'kind': kind}).data
    assert [r['name'] for r in res['results']] == [expected]


def test_status_filter(staff, data):
    assert {r['name'] for r in staff.get(LIST, {'status': 'new'}).data['results']} == {'Gina', 'Thandi Mokoena'}
    assert [r['name'] for r in staff.get(LIST, {'status': 'handled'}).data['results']] == ['Sam']


@pytest.mark.parametrize('q,expected', [
    ('urban', ['Gina']), ('thandi mok', ['Thandi Mokoena']), ('sam@', ['Sam']), ('nobody', []),
])
def test_search(staff, data, q, expected):
    assert [r['name'] for r in staff.get(LIST, {'q': q}).data['results']] == expected


def test_paging(staff):
    for i in range(30):
        quote(f'P{i}', ago=i)
    first = staff.get(LIST).data
    assert len(first['results']) == 25 and first['total_pages'] == 2 and first['count'] == 30
    second = staff.get(LIST, {'page': 2}).data
    assert [r['name'] for r in second['results']] == [f'P{i}' for i in range(25, 30)]
    assert staff.get(LIST, {'page': 'x'}).data['page'] == 1


def test_solar_detail(staff, data):
    res = staff.get(f'/quote-request/staff/solar/{data[1].pk}/').data
    titles = [s['title'] for s in res['sections']]
    assert titles == ['Contact', 'Request', 'Property', 'Needs & budget']
    rows = {r['label']: r['value'] for s in res['sections'] for r in s['rows']}
    assert rows['Monthly electricity bill'] == 'R 3 500'
    assert rows['Province'] == 'Western Cape' and rows['Budget range'] == 'R50k - R150k'
    assert res['message'] == 'North-facing roof'


def test_container_detail(staff, data):
    res = staff.get(f'/quote-request/staff/container/{data[2].pk}/').data
    rows = {r['label']: r['value'] for s in res['sections'] for r in s['rows']}
    assert rows['Add-ons'] == 'Ablution unit, Air conditioning'
    assert rows['Delivery / export address'] == '12 Dock Rd, Cape Town'
    assert res['message'] == 'Two windows please'


def test_general_detail_and_wrong_kind(staff, data):
    res = staff.get(f'/quote-request/staff/general/{data[0].pk}/').data
    assert res['message'] == 'Need a DB board check'
    # A solar quote isn't reachable as "general", and unknown kinds 404.
    assert staff.get(f'/quote-request/staff/general/{data[1].pk}/').status_code == 404
    assert staff.get(f'/quote-request/staff/other/{data[0].pk}/').status_code == 404
    assert staff.get('/quote-request/staff/container/999999/').status_code == 404
