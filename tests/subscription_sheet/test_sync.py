"""
Tests for the APS Subscriptions sheet sync (apps.subscription_sheet).

The Apps Script POST is always mocked. Covers: the payfast_token validity
filter, field mapping for both plans, plan-specific columns, upsert dedup
records, failure handling, and the config guard.
"""

from datetime import datetime, timezone as dt_timezone
from unittest import mock

import pytest

from apps.request_solar_cleaning.models import (
    Client as CleaningClient, Subscription as CleaningSub,
)
from apps.subscription.models import Client as InvClient, Subscription as InvSub
from apps.subscription_sheet import exporter
from apps.subscription_sheet.models import ExportedSubscription


@pytest.fixture
def sheet_settings(settings):
    settings.APS_SUBSCRIPTION_SHEET_URL = 'https://script.google/exec'
    settings.APS_SUBSCRIPTION_SHEET_TOKEN = 'secret'
    return settings


def _ok(created=0, updated=0):
    m = mock.MagicMock()
    m.json.return_value = {'ok': True, 'created': created, 'updated': updated}
    m.raise_for_status.return_value = None
    return m


def _inv_sub(token='tok', **kw):
    c = InvClient.objects.create(name='Rob B', email='rob@x.co', phone='0210000000')
    defaults = dict(client=c, inverter_type='Deye', address='1 Main Rd',
                    subscription_length=12, call_out_balance=3, payfast_token=token,
                    is_active=True)
    defaults.update(kw)
    return InvSub.objects.create(**defaults)


def _clean_sub(token='tok', **kw):
    c = CleaningClient.objects.create(name='Hazel', email='hazel@x.co', phone='0219999999')
    defaults = dict(client=c, inverter_type='Sunsynk', inverter_size='8kW',
                    installed_panels_count='12', address='2 Beach Rd',
                    subscription_length=6, payfast_token=token, is_active=True)
    defaults.update(kw)
    return CleaningSub.objects.create(**defaults)


@pytest.mark.django_db
class TestSubscriptionSync:

    def test_only_valid_payfast_subs_exported(self, sheet_settings):
        _inv_sub(token='valid1')
        _inv_sub(token='')       # no token -> excluded
        _inv_sub(token=None)     # null token -> excluded
        _clean_sub(token='valid2')
        with mock.patch('apps.subscription_sheet.exporter.requests.post',
                        return_value=_ok(created=2)) as post:
            stats = exporter.sync_subscriptions()
        assert stats['sent'] == 2
        payload = post.call_args.kwargs['json']
        assert payload['type'] == 'subscriptions'
        assert payload['token'] == 'secret'
        keys = {s['key'] for s in payload['subscriptions']}
        assert all(':' in k for k in keys)

    def test_inactive_subs_excluded(self, sheet_settings):
        _inv_sub(token='t1', is_active=True)   # exported
        _inv_sub(token='t2', is_active=False)  # inactive -> excluded even with token
        with mock.patch('apps.subscription_sheet.exporter.requests.post',
                        return_value=_ok(created=1)) as post:
            stats = exporter.sync_subscriptions()
        assert stats['sent'] == 1
        subs = post.call_args.kwargs['json']['subscriptions']
        assert len(subs) == 1

    def test_field_mapping_both_plans(self, sheet_settings):
        _inv_sub(token='t1')
        _clean_sub(token='t2')
        with mock.patch('apps.subscription_sheet.exporter.requests.post',
                        return_value=_ok(created=2)) as post:
            exporter.sync_subscriptions()
        subs = {s['plan']: s for s in post.call_args.kwargs['json']['subscriptions']}

        mon = subs['Inverter & Battery Monitoring Plan']
        assert mon['key'].startswith('monitoring:')
        assert mon['client_name'] == 'Rob B'
        assert mon['call_out_balance'] == 3
        assert mon['inverter_size'] == ''        # not on this plan
        assert mon['installed_panels'] == ''

        mnt = subs['Solar & Inverter Maintenance Plan']
        assert mnt['key'].startswith('maintenance:')
        assert mnt['inverter_size'] == '8kW'
        assert mnt['installed_panels'] == '12'
        assert mnt['call_out_balance'] is None   # not on this plan

    def test_excludes_sensitive_fields(self, sheet_settings):
        _inv_sub(token='t1', payfast_payment_id='pf-123')
        with mock.patch('apps.subscription_sheet.exporter.requests.post',
                        return_value=_ok(created=1)) as post:
            exporter.sync_subscriptions()
        item = post.call_args.kwargs['json']['subscriptions'][0]
        for banned in ('payfast_token', 'payfast_payment_id', 'is_active', 'id'):
            assert banned not in item

    def test_records_dedup_rows(self, sheet_settings):
        _inv_sub(token='t1')
        _clean_sub(token='t2')
        with mock.patch('apps.subscription_sheet.exporter.requests.post',
                        return_value=_ok(created=2)):
            exporter.sync_subscriptions()
        assert ExportedSubscription.objects.count() == 2
        assert set(ExportedSubscription.objects.values_list('app_label', flat=True)) == {
            'monitoring', 'maintenance'}

    def test_resync_is_idempotent(self, sheet_settings):
        _inv_sub(token='t1')
        with mock.patch('apps.subscription_sheet.exporter.requests.post',
                        return_value=_ok(created=1)):
            exporter.sync_subscriptions()
        with mock.patch('apps.subscription_sheet.exporter.requests.post',
                        return_value=_ok(updated=1)):
            exporter.sync_subscriptions()
        # Still exactly one dedup row (update_or_create, not duplicate).
        assert ExportedSubscription.objects.count() == 1

    def test_failed_post_records_error(self, sheet_settings):
        _inv_sub(token='t1')
        with mock.patch('apps.subscription_sheet.exporter.requests.post',
                        side_effect=Exception('network down')):
            stats = exporter.sync_subscriptions()
        assert stats['error'] is not None
        # No dedup rows recorded on failure.
        assert ExportedSubscription.objects.count() == 0

    def test_sheet_rejection_records_error(self, sheet_settings):
        _inv_sub(token='t1')
        bad = mock.MagicMock()
        bad.json.return_value = {'ok': False, 'error': 'unauthorized'}
        bad.raise_for_status.return_value = None
        with mock.patch('apps.subscription_sheet.exporter.requests.post', return_value=bad):
            stats = exporter.sync_subscriptions()
        assert stats['error'] == 'unauthorized'
        assert ExportedSubscription.objects.count() == 0

    def test_nothing_to_send(self, sheet_settings):
        with mock.patch('apps.subscription_sheet.exporter.requests.post') as post:
            stats = exporter.sync_subscriptions()
        assert stats['sent'] == 0
        post.assert_not_called()


@pytest.mark.django_db
class TestConfigGuard:
    def test_missing_config_raises(self, settings):
        settings.APS_SUBSCRIPTION_SHEET_URL = ''
        settings.APS_SUBSCRIPTION_SHEET_TOKEN = ''
        _inv_sub(token='t1')
        with pytest.raises(exporter.SubscriptionSheetConfigError):
            exporter.sync_subscriptions()
