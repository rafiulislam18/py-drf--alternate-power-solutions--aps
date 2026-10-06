"""Tickets pick one of APS's real services, live from Site content → Services.
The chosen service's name is stored as text (no link to the services table)."""

import pytest

from apps.client_portal.models import Ticket
from apps.services_and_projects.models import Service

from .conftest import make_service, make_ticket

TICKETS_URL = '/client-portal/tickets/'


def payload(site, service):
    return {'service': service, 'title': 'Job', 'description': 'Details', 'site': site.pk}


def test_me_lists_the_live_services_in_site_order(api):
    make_service('Plumbing')
    top = make_service('Solar')
    Service.objects.filter(pk=top.pk).update(appreciation_mark=10)
    services = api.get('/client-portal/me/').data['services']
    assert [s['title'] for s in services] == ['Solar', 'Plumbing']
    assert services[0]['image'].startswith('http')
    assert set(services[0]) == {'id', 'title', 'image'}


def test_new_service_shows_up_straight_away(api):
    assert api.get('/client-portal/me/').data['services'] == []
    make_service('EV Charger Installation')
    assert [s['title'] for s in api.get('/client-portal/me/').data['services']] == ['EV Charger Installation']


def test_ticket_stores_the_service_name(api, site):
    solar = make_service('Solar Installation')
    res = api.post(TICKETS_URL, payload(site, solar.pk), format='json')
    assert res.status_code == 201, res.data
    assert res.data['service'] == 'Solar Installation'
    assert Ticket.objects.get().service == 'Solar Installation'


@pytest.mark.parametrize('bad', ['', '999999', 'other', 'electrical', None])
def test_only_real_services_accepted(api, site, bad):
    make_service('Electrical')
    data = payload(site, bad)
    if bad is None:
        data.pop('service')
    res = api.post(TICKETS_URL, data, format='json')
    assert res.status_code == 400
    assert 'service' in res.data
    assert not Ticket.objects.exists()


def test_not_linked_to_the_services_table(api, client_user, site):
    painting = make_service('Painting')
    res = api.post(TICKETS_URL, payload(site, painting.pk), format='json')
    ticket_id = res.data['id']
    Service.objects.filter(pk=painting.pk).update(title='Painting & coatings')
    assert api.get(f'{TICKETS_URL}{ticket_id}/').data['service'] == 'Painting'
    painting.delete()  # removing a service doesn't touch tickets
    assert api.get(f'{TICKETS_URL}{ticket_id}/').data['service'] == 'Painting'


def test_search_matches_service_name(api, client_user, site):
    make_ticket(client_user, site, title='Leak', service='Plumbing')
    make_ticket(client_user, site, title='Panels', service='Solar')
    res = api.get(TICKETS_URL, {'search': 'plumb'})
    assert [t['title'] for t in res.data['results']] == ['Leak']
