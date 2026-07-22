"""
Tests for the quotes-sheet sync (apps.quote_sheet).

The Apps Script POST is always mocked. Covers: the 3-way split (non-solar /
solar / container) by service title, solar-details flattening, container
field/choice mapping, per-tab routing via the `type` field, upsert dedup
records, failure handling, and the config guard.
"""

from unittest import mock

import pytest

from apps.services_and_projects.models import Service
from apps.quote_request.models import QuoteRequest, SolarQuoteDetails
from apps.container_conversion.models import ServiceRequest
from apps.quote_sheet import exporter
from apps.quote_sheet.models import ExportedQuote


@pytest.fixture
def sheet_settings(settings):
    settings.APS_QUOTE_SHEET_URL = 'https://script.google/exec'
    settings.APS_QUOTE_SHEET_TOKEN = 'secret'
    return settings


def _ok(created=0, updated=0):
    m = mock.MagicMock()
    m.json.return_value = {'ok': True, 'created': created, 'updated': updated}
    m.raise_for_status.return_value = None
    return m


def _service(title):
    return Service.objects.create(
        title=title, short_description='s', long_description='l', image='x.jpg',
    )


def _solar_service():
    return _service(exporter.SOLAR_SERVICE_TITLE)


def _quote(service=None, **kw):
    """A NON-solar quote by default (Plumbing service)."""
    defaults = dict(name='Jane Doe', phone='0210000000', email='jane@x.co',
                    service=service or _service('Plumbing'))
    defaults.update(kw)
    return QuoteRequest.objects.create(**defaults)


def _solar_quote(**kw):
    return _quote(service=_solar_service(), **kw)


def _container(**kw):
    defaults = dict(
        first_name='Sam', last_name='Nkosi', email='sam@x.co', phone='0821112222',
        unit_type='container', intended_use='office', modular_size='6m',
        transport_or_export_address='5 Dock Rd, Cape Town',
    )
    defaults.update(kw)
    return ServiceRequest.objects.create(**defaults)


def _by_type(post):
    """Map POST 'type' -> the quotes list sent for it."""
    return {c.kwargs['json']['type']: c.kwargs['json']['quotes']
            for c in post.call_args_list}


@pytest.mark.django_db
class TestQuoteSync:

    def test_split_by_service_across_three_tabs(self, sheet_settings):
        _quote()                       # non-solar -> regular
        _quote(name='Bob', email='bob@x.co', sent_quote=True)
        _solar_quote(name='Sol', email='sol@x.co')   # -> solar
        _container()                   # -> container
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        return_value=_ok(created=1)) as post:
            results = exporter.sync_quotes()
        assert results['regular']['sent'] == 2
        assert results['solar']['sent'] == 1
        assert results['container']['sent'] == 1
        types = set(c.kwargs['json']['type'] for c in post.call_args_list)
        assert types == {'quote_requests', 'solar_quotes', 'container_quotes'}

    def test_solar_never_in_regular_tab(self, sheet_settings):
        _quote()                       # non-solar
        _solar_quote(name='Sol', email='sol@x.co')
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        return_value=_ok(created=1)) as post:
            exporter.sync_quotes()
        sent = _by_type(post)
        reg_keys = {q['key'] for q in sent['quote_requests']}
        sol_keys = {q['key'] for q in sent['solar_quotes']}
        assert all(k.startswith('regular:') for k in reg_keys)
        assert all(k.startswith('solar:') for k in sol_keys)
        assert reg_keys.isdisjoint(sol_keys)

    def test_regular_has_no_solar_columns(self, sheet_settings):
        _quote()
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        return_value=_ok(created=1)) as post:
            exporter.sync_quotes()
        item = _by_type(post)['quote_requests'][0]
        assert item['name'] == 'Jane Doe'
        assert item['service'] == 'Plumbing'
        for solar_col in ('property_type', 'province', 'monthly_bill', 'suburb'):
            assert solar_col not in item

    def test_solar_details_flattened(self, sheet_settings):
        q = _solar_quote()
        SolarQuoteDetails.objects.create(
            quote_request=q, property_type='residential', suburb='Wynberg',
            province='western_cape', roof_type='tiled', monthly_bill=2500,
            primary_goal='backup', grid_connection='single_phase_eskom',
            budget_range='50-150k', timeline='asap', referral_source='google',
            additional_notes='urgent',
        )
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        return_value=_ok(created=1)) as post:
            exporter.sync_quotes()
        item = _by_type(post)['solar_quotes'][0]
        assert item['key'].startswith('solar:')
        assert item['property_type'] == 'Residential'
        assert item['province'] == 'Western Cape'
        assert item['roof_type'] == 'Tiled'
        assert item['primary_goal'] == 'Backup power'
        assert item['monthly_bill'] == 2500
        assert item['additional_notes'] == 'urgent'

    def test_solar_without_details_blank(self, sheet_settings):
        _solar_quote()   # no SolarQuoteDetails row
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        return_value=_ok(created=1)) as post:
            exporter.sync_quotes()
        item = _by_type(post)['solar_quotes'][0]
        assert item['property_type'] == ''
        assert item['monthly_bill'] == ''
        assert item['suburb'] == ''

    def test_container_field_mapping(self, sheet_settings):
        _container(electrical_installation=True, solar_backup_power=True,
                   budget_range='250_to_500k', finish_level='premium',
                   project_timeframe='urgent', is_processed=True)
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        return_value=_ok(created=1)) as post:
            exporter.sync_quotes()
        item = _by_type(post)['container_quotes'][0]
        assert item['key'].startswith('container:')
        assert item['unit_type'] == 'Container'
        assert item['intended_use'] == 'Office'
        assert item['modular_size'] == '6 Meter (20ft Container)'
        assert item['electrical_installation'] == 'Yes'
        assert item['plumbing_installation'] == 'No'
        assert item['solar_backup_power'] == 'Yes'
        assert item['budget_range'] == 'R250,000 – R500,000'
        assert item['finish_level'] == 'Premium'
        assert item['project_timeframe'] == 'Urgent'
        assert item['is_processed'] == 'Yes'

    def test_records_dedup_rows(self, sheet_settings):
        _quote()
        _solar_quote(name='Sol', email='sol@x.co')
        _container()
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        return_value=_ok(created=1)):
            exporter.sync_quotes()
        assert ExportedQuote.objects.count() == 3
        assert set(ExportedQuote.objects.values_list('kind', flat=True)) == {
            'regular', 'solar', 'container'}

    def test_resync_is_idempotent(self, sheet_settings):
        _solar_quote()
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        return_value=_ok(created=1)):
            exporter.sync_quotes()
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        return_value=_ok(updated=1)):
            exporter.sync_quotes()
        assert ExportedQuote.objects.filter(kind='solar').count() == 1

    def test_failed_post_records_error_no_dedup(self, sheet_settings):
        _quote()
        with mock.patch('apps.quote_sheet.exporter.requests.post',
                        side_effect=Exception('network down')):
            results = exporter.sync_quotes()
        assert results['regular']['error'] is not None
        assert ExportedQuote.objects.count() == 0

    def test_sheet_rejection_records_error(self, sheet_settings):
        _quote()
        bad = mock.MagicMock()
        bad.json.return_value = {'ok': False, 'error': 'unauthorized'}
        bad.raise_for_status.return_value = None
        with mock.patch('apps.quote_sheet.exporter.requests.post', return_value=bad):
            results = exporter.sync_quotes()
        assert results['regular']['error'] == 'unauthorized'
        assert ExportedQuote.objects.count() == 0

    def test_nothing_to_send(self, sheet_settings):
        with mock.patch('apps.quote_sheet.exporter.requests.post') as post:
            results = exporter.sync_quotes()
        assert results['regular']['sent'] == 0
        assert results['solar']['sent'] == 0
        assert results['container']['sent'] == 0
        post.assert_not_called()


@pytest.mark.django_db
class TestConfigGuard:
    def test_missing_config_raises(self, settings):
        settings.APS_QUOTE_SHEET_URL = ''
        settings.APS_QUOTE_SHEET_TOKEN = ''
        _quote()
        with pytest.raises(exporter.QuoteSheetConfigError):
            exporter.sync_quotes()
