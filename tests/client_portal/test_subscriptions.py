"""
Tests for the client dashboard's Subscriptions page endpoints.

Subscriptions are matched on the account's email, and only once that email is
verified (a confirmation or reset link was clicked) — so typing someone else's
address into an account never shows, or lets it cancel, their plans. A client
only ever sees plans paid under its OWN verified email. The PayFast call is
patched out; the shared cancel logic itself is covered by
tests/subscription_portal.
"""

from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.request_solar_cleaning.models import Client as SolarClient, Subscription as SolarSub
from apps.subscription.models import (
    Client as InverterClient,
    Payment as InverterPayment,
    Subscription as InverterSub,
)

from .conftest import api_for, make_client

LIST_URL = '/client-portal/subscriptions/'
PAYMENTS_URL = '/client-portal/subscriptions/payments/'
CANCEL_URL = '/client-portal/subscriptions/cancel/'


def _sub(email, provider='inverter', active=True):
    client_model, sub_model = (
        (InverterClient, InverterSub) if provider == 'inverter' else (SolarClient, SolarSub)
    )
    client = client_model.objects.create(name='Payer', email=email, phone='0110000000')
    return sub_model.objects.create(
        client=client,
        address='12 Main Rd, Cape Town',
        payfast_token=f'tok-{provider}-{email}',
        is_active=active,
        subscription_length=3,
        last_payment_date=timezone.now(),
    )


def _pay(sub, pf):
    return InverterPayment.objects.create(
        client=sub.client, subscription=sub, amount_gross='99.00', amount_fee='3.45',
        amount_net='95.55', pf_payment_id=pf, m_payment_id=str(sub.id),
        payment_status='COMPLETE', item_name='Monthly Subscription', raw_payload={},
    )


@pytest.fixture
def unverified(db):
    return make_client('lerato-co', email='lerato@acme.example', company_name='Lerato Co', verified=False)


def test_lists_both_apps_for_own_verified_email_only(client_user, other_client):
    _sub('SIPHO@acme.example', 'inverter')  # matched case-insensitively
    _sub(client_user.email, 'solar', active=False)
    _sub(other_client.email, 'inverter')

    res = api_for(client_user).get(LIST_URL)
    assert res.status_code == 200
    assert res.data['email'] == client_user.email
    assert res.data['email_verified'] is True
    subs = res.data['subscriptions']
    assert sorted(s['provider'] for s in subs) == ['inverter', 'solar']
    assert all(s['ref'] for s in subs)
    assert {s['isActive'] for s in subs} == {True, False}


def test_payments_for_own_verified_email_only(client_user, other_client):
    _pay(_sub(client_user.email), 'pf-mine')
    _pay(_sub(other_client.email), 'pf-theirs')
    res = api_for(client_user).get(PAYMENTS_URL)
    assert res.status_code == 200
    assert res.data['email_verified'] is True
    assert [p['pfPaymentId'] for p in res.data['payments']] == ['pf-mine']
    assert res.data['payments'][0]['currency'] == 'ZAR'


def test_unverified_email_shows_nothing(unverified):
    sub = _sub(unverified.email)
    _pay(sub, 'pf-hidden')
    api = api_for(unverified)

    listed = api.get(LIST_URL)
    assert listed.status_code == 200
    assert listed.data == {'email': '', 'email_verified': False, 'subscriptions': []}

    payments = api.get(PAYMENTS_URL)
    assert payments.status_code == 200
    assert payments.data == {'email': '', 'email_verified': False, 'payments': []}


@patch('apps.subscription_portal.cancellation.cancel_payfast_subscription', return_value=True)
def test_unverified_email_cannot_cancel(mock_cancel, unverified):
    sub = _sub(unverified.email)
    # Even holding a genuine ref (e.g. from the public portal) doesn't help.
    from apps.subscription_portal.providers import gather_subscriptions

    ref = gather_subscriptions(unverified.email)[0].ref
    res = api_for(unverified).post(CANCEL_URL, {'ref': ref}, format='json')
    assert res.status_code == 403
    mock_cancel.assert_not_called()
    sub.refresh_from_db()
    assert sub.is_active


def test_email_edited_to_someone_elses_address_shows_nothing(client_user, other_client):
    # An admin retypes the account email: it's unverified again until confirmed,
    # so the new address's plans stay hidden.
    _sub(other_client.email)
    client_user.email = other_client.email
    client_user.save()
    res = api_for(client_user).get(LIST_URL)
    assert res.data['email_verified'] is False
    assert res.data['subscriptions'] == []


def test_pending_email_change_does_not_expose_new_address(client_user):
    _sub('new@acme.example')
    profile = client_user.client_profile
    profile.pending_email = 'new@acme.example'
    profile.save()
    res = api_for(client_user).get(LIST_URL)
    assert res.data['email'] == 'sipho@acme.example'
    assert res.data['subscriptions'] == []


def test_profileless_client_shows_nothing(user):
    _sub(user.email)
    res = api_for(user).get(LIST_URL)
    assert res.status_code == 200
    assert res.data['subscriptions'] == []


@patch('apps.subscription_portal.cancellation.cancel_payfast_subscription', return_value=True)
def test_cancel_own_subscription(mock_cancel, client_user):
    sub = _sub(client_user.email)
    api = api_for(client_user)
    ref = api.get(LIST_URL).data['subscriptions'][0]['ref']
    res = api.post(CANCEL_URL, {'ref': ref}, format='json')
    assert res.status_code == 200
    mock_cancel.assert_called_once_with(sub.payfast_token)
    sub.refresh_from_db()
    assert not sub.is_active

    again = api.post(CANCEL_URL, {'ref': ref}, format='json')
    assert again.status_code == 400  # already inactive
    assert mock_cancel.call_count == 1


@patch('apps.subscription_portal.cancellation.cancel_payfast_subscription', return_value=True)
def test_cannot_cancel_another_clients_subscription(mock_cancel, client_user, other_client):
    sub = _sub(other_client.email)
    their_ref = api_for(other_client).get(LIST_URL).data['subscriptions'][0]['ref']
    res = api_for(client_user).post(CANCEL_URL, {'ref': their_ref}, format='json')
    assert res.status_code == 403
    mock_cancel.assert_not_called()
    sub.refresh_from_db()
    assert sub.is_active


@patch('apps.subscription_portal.cancellation.cancel_payfast_subscription', return_value=False)
def test_payfast_failure_leaves_subscription_active(mock_cancel, client_user):
    sub = _sub(client_user.email)
    api = api_for(client_user)
    ref = api.get(LIST_URL).data['subscriptions'][0]['ref']
    res = api.post(CANCEL_URL, {'ref': ref}, format='json')
    assert res.status_code == 502
    sub.refresh_from_db()
    assert sub.is_active


def test_cancel_rejects_tampered_ref(client_user):
    res = api_for(client_user).post(CANCEL_URL, {'ref': 'garbage'}, format='json')
    assert res.status_code == 400


def test_cancel_requires_a_ref(client_user):
    res = api_for(client_user).post(CANCEL_URL, {}, format='json')
    assert res.status_code == 400


def test_staff_cannot_use_subscription_endpoints(staff_user):
    _sub(staff_user.email)
    api = api_for(staff_user)
    assert api.get(LIST_URL).status_code == 403
    assert api.get(PAYMENTS_URL).status_code == 403
    assert api.post(CANCEL_URL, {'ref': 'x'}, format='json').status_code == 403


def test_endpoints_require_sign_in(db):
    assert api_for().get(LIST_URL).status_code == 401
    assert api_for().get(PAYMENTS_URL).status_code == 401
    assert api_for().post(CANCEL_URL, {'ref': 'x'}, format='json').status_code == 401
