"""
Tests for PayFast ITN payment recording on the R99 inverter-monitoring plan.

The ITN handler now writes an immutable Payment row for every COMPLETE
notification and uses it as the idempotency key. That matters because PayFast
retries ITNs: before this, a retry would re-increment the month counter and
hand out loyalty call-outs early.

Source IP verification and the admin notification email are patched out so the
tests never touch the network.
"""

from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.subscription.models import Client, Payment, Subscription

NOTIFY_URL = '/subscription/payfast-notify/'


@pytest.fixture
def subscription(db):
    client = Client.objects.create(
        name='Sub One', email='subscriber@example.com', phone='0110000000'
    )
    return Subscription.objects.create(
        client=client,
        address='12 Main Rd, Cape Town',
        is_active=False,
        subscription_length=0,
    )


def itn_payload(subscription, **overrides):
    payload = {
        'm_payment_id': str(subscription.id),
        'pf_payment_id': 'pf-12345',
        'payment_status': 'COMPLETE',
        'item_name': 'Monthly Subscription',
        'amount_gross': '99.00',
        'amount_fee': '3.45',
        'amount_net': '95.55',
        'token': 'tok-abc-123',
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _no_network():
    """PayFast IP checks and admin emails are irrelevant to these assertions."""
    with patch('apps.subscription.views.verify_payfast_ip', return_value=True), \
         patch('apps.subscription.views.EmailMessage'):
        yield


def post_itn(client, subscription, **overrides):
    return client.post(NOTIFY_URL, data=itn_payload(subscription, **overrides))


def test_complete_itn_records_payment(client, subscription):
    resp = post_itn(client, subscription)
    assert resp.status_code == 200

    payment = Payment.objects.get(pf_payment_id='pf-12345')
    assert payment.subscription == subscription
    assert payment.client == subscription.client
    assert str(payment.amount_gross) == '99.00'
    assert str(payment.amount_fee) == '3.45'
    assert str(payment.amount_net) == '95.55'
    assert payment.payment_status == 'COMPLETE'
    assert payment.item_name == 'Monthly Subscription'
    assert payment.payfast_token == 'tok-abc-123'
    assert payment.raw_payload['pf_payment_id'] == 'pf-12345'


def test_complete_itn_activates_and_counts_month(client, subscription):
    post_itn(client, subscription)

    subscription.refresh_from_db()
    assert subscription.is_active is True
    assert subscription.subscription_length == 1
    assert subscription.payfast_token == 'tok-abc-123'
    assert subscription.last_payment_date is not None


def test_duplicate_itn_records_one_payment(client, subscription):
    """PayFast retries ITNs — the same pf_payment_id must not double-record."""
    post_itn(client, subscription)
    resp = post_itn(client, subscription)

    assert resp.status_code == 200
    assert Payment.objects.filter(pf_payment_id='pf-12345').count() == 1


def test_duplicate_itn_does_not_double_count_months(client, subscription):
    post_itn(client, subscription)
    post_itn(client, subscription)

    subscription.refresh_from_db()
    assert subscription.subscription_length == 1


def test_distinct_payments_each_recorded(client, subscription):
    post_itn(client, subscription, pf_payment_id='pf-month-1')
    post_itn(client, subscription, pf_payment_id='pf-month-2')

    assert Payment.objects.count() == 2

    subscription.refresh_from_db()
    assert subscription.subscription_length == 2


def test_twelfth_payment_grants_call_outs(client, subscription):
    for month in range(1, 13):
        post_itn(client, subscription, pf_payment_id=f'pf-month-{month}')

    subscription.refresh_from_db()
    assert subscription.subscription_length == 12
    assert subscription.call_out_balance == 2
    assert Payment.objects.count() == 12


def test_wrong_amount_records_nothing(client, subscription):
    resp = post_itn(client, subscription, amount_gross='1.00')

    assert resp.status_code == 400
    assert Payment.objects.count() == 0

    subscription.refresh_from_db()
    assert subscription.is_active is False


def test_non_complete_status_records_nothing(client, subscription):
    resp = post_itn(client, subscription, payment_status='FAILED')

    assert resp.status_code == 200
    assert Payment.objects.count() == 0

    subscription.refresh_from_db()
    assert subscription.subscription_length == 0
