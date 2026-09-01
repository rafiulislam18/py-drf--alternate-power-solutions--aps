"""
Tests for the portal's payment-history endpoint.

Covers the scoped-token gate, cross-app aggregation (both provider apps in one
newest-first list), and the isolation guarantee that a token only ever sees the
payments of its own email.
"""

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.subscription.models import (
    Client as InverterClient,
    Payment as InverterPayment,
    Subscription as InverterSub,
)
from apps.request_solar_cleaning.models import (
    Client as SolarClient,
    Payment as SolarPayment,
    Subscription as SolarSub,
)
from apps.subscription_portal.tokens import issue_portal_token

PAYMENTS_URL = '/subscription-portal/payments/'

EMAIL = 'subscriber@example.com'
OTHER_EMAIL = 'someone.else@example.com'


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def auth(email=EMAIL):
    return {'HTTP_AUTHORIZATION': f'Bearer {issue_portal_token(email)}'}


@pytest.fixture
def inverter_payment(db):
    client = InverterClient.objects.create(name='Sub One', email=EMAIL, phone='0110000000')
    sub = InverterSub.objects.create(
        client=client,
        address='12 Main Rd, Cape Town',
        is_active=True,
        subscription_length=2,
        last_payment_date=timezone.now(),
    )
    return InverterPayment.objects.create(
        client=client,
        subscription=sub,
        amount_gross='99.00',
        amount_fee='3.45',
        amount_net='95.55',
        pf_payment_id='pf-inv-1',
        m_payment_id=str(sub.id),
        payment_status='COMPLETE',
        item_name='Monthly Subscription',
        raw_payload={'payment_status': 'COMPLETE'},
    )


@pytest.fixture
def solar_payment(db):
    client = SolarClient.objects.create(name='Sub One', email=EMAIL, phone='0110000000')
    sub = SolarSub.objects.create(
        client=client,
        address='12 Main Rd, Cape Town',
        is_active=True,
        subscription_length=1,
    )
    return SolarPayment.objects.create(
        client=client,
        subscription=sub,
        amount_gross='199.00',
        pf_payment_id='pf-sol-1',
        m_payment_id=str(sub.id),
        payment_status='COMPLETE',
        item_name='Monthly Subscription',
    )


def test_payments_requires_token(client, db):
    assert client.get(PAYMENTS_URL).status_code == 401


def test_payments_rejects_garbage_token(client, db):
    resp = client.get(PAYMENTS_URL, HTTP_AUTHORIZATION='Bearer not-a-token')
    assert resp.status_code == 401


def test_payments_empty_for_unknown_email(client, db):
    resp = client.get(PAYMENTS_URL, **auth())
    assert resp.status_code == 200
    assert resp.json()['payments'] == []


def test_payments_lists_across_both_apps(client, inverter_payment, solar_payment):
    resp = client.get(PAYMENTS_URL, **auth())
    assert resp.status_code == 200

    body = resp.json()
    assert body['email'] == EMAIL

    refs = {p['pfPaymentId'] for p in body['payments']}
    assert refs == {'pf-inv-1', 'pf-sol-1'}

    providers = {p['provider'] for p in body['payments']}
    assert providers == {'inverter', 'solar'}


def test_payments_serialises_amounts_and_currency(client, inverter_payment):
    payment = client.get(PAYMENTS_URL, **auth()).json()['payments'][0]

    assert payment['amountGross'] == '99.00'
    assert payment['amountFee'] == '3.45'
    assert payment['amountNet'] == '95.55'
    assert payment['currency'] == 'ZAR'
    assert payment['status'] == 'COMPLETE'
    assert payment['itemName'] == 'Monthly Subscription'
    assert payment['planLabel']
    assert payment['date']


def test_payments_newest_first(client, inverter_payment, solar_payment):
    payments = client.get(PAYMENTS_URL, **auth()).json()['payments']
    dates = [p['date'] for p in payments]
    assert dates == sorted(dates, reverse=True)


def test_payments_isolated_per_email(client, inverter_payment):
    """A valid token must never surface another subscriber's payments."""
    resp = client.get(PAYMENTS_URL, **auth(OTHER_EMAIL))
    assert resp.status_code == 200
    assert resp.json()['payments'] == []


def test_payments_matches_email_case_insensitively(client, inverter_payment):
    resp = client.get(PAYMENTS_URL, **auth(EMAIL.upper()))
    assert resp.status_code == 200
    assert len(resp.json()['payments']) == 1
