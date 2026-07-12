"""
Tests for the fault-alert pickup flow (apps.fault_detection).

Covers the public pickup API: reading an alert's status, claiming it once,
the one-shot lock (second claim conflicts), and that the "picked up by X"
confirmation is dispatched exactly once on a successful claim.
"""

from unittest import mock

import pytest

from apps.fault_detection.models import FaultAlert


@pytest.fixture
def alert(db):
    return FaultAlert.objects.create(kind='soc_imbalance', summary='spread 12% exceeds 10%')


def _url(alert):
    return f'/fault-detection/alerts/{alert.uuid}/pickup/'


@pytest.mark.views
class TestFaultAlertPickup:

    def test_get_unclaimed_alert(self, api_client, alert):
        res = api_client.get(_url(alert))
        assert res.status_code == 200
        assert res.data['is_picked_up'] is False
        assert res.data['picked_up_by'] == ''
        assert res.data['kind_display'] == 'SOC Imbalance'

    def test_get_unknown_alert_404(self, api_client, db):
        res = api_client.get('/fault-detection/alerts/00000000-0000-0000-0000-000000000000/pickup/')
        assert res.status_code == 404

    def test_blank_name_rejected(self, api_client, alert):
        res = api_client.post(_url(alert), {'name': '   '}, format='json')
        assert res.status_code == 400

    def test_claim_records_name_and_dispatches_once(self, api_client, alert):
        with mock.patch('apps.fault_detection.views.dispatch_pickup_confirmation') as m:
            res = api_client.post(_url(alert), {'name': 'Thabo'}, format='json')
        assert res.status_code == 200
        assert res.data['is_picked_up'] is True
        assert res.data['picked_up_by'] == 'Thabo'
        assert res.data['picked_up_at'] is not None
        assert m.call_count == 1

        alert.refresh_from_db()
        assert alert.picked_up_by == 'Thabo'
        assert alert.picked_up_at is not None

    def test_second_claim_conflicts_and_keeps_first(self, api_client, alert):
        with mock.patch('apps.fault_detection.views.dispatch_pickup_confirmation'):
            first = api_client.post(_url(alert), {'name': 'Thabo'}, format='json')
        assert first.status_code == 200

        with mock.patch('apps.fault_detection.views.dispatch_pickup_confirmation') as m:
            second = api_client.post(_url(alert), {'name': 'Sipho'}, format='json')
        assert second.status_code == 409
        assert second.data['error'] == 'already_picked_up'
        assert second.data['alert']['picked_up_by'] == 'Thabo'
        # No duplicate confirmation for the losing claim.
        assert m.call_count == 0

        alert.refresh_from_db()
        assert alert.picked_up_by == 'Thabo'

    def test_name_is_trimmed(self, api_client, alert):
        with mock.patch('apps.fault_detection.views.dispatch_pickup_confirmation'):
            res = api_client.post(_url(alert), {'name': '  Nomsa  '}, format='json')
        assert res.status_code == 200
        assert res.data['picked_up_by'] == 'Nomsa'


@pytest.mark.models
class TestFaultAlertModel:

    def test_is_picked_up_property(self, db):
        a = FaultAlert.objects.create(kind='charge_behaviour', summary='x')
        assert a.is_picked_up is False
        a.picked_up_by = 'Ayanda'
        assert a.is_picked_up is True

    def test_uuid_is_unique_and_set(self, db):
        a = FaultAlert.objects.create(kind='soc_imbalance', summary='x')
        b = FaultAlert.objects.create(kind='soc_imbalance', summary='y')
        assert a.uuid != b.uuid
