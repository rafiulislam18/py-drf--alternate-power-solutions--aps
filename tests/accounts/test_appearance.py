"""
Dashboard appearance saved to the account (apps.accounts.appearance):
PUT /accounts/me/appearance/, and ``appearance`` on /accounts/me/ and the
sign-in response. Theme ids are only format-checked; fonts, densities, text
sizes and the keys are strict. Saves aren't written to the audit history.
"""

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.accounts.appearance import DEFAULTS
from apps.accounts.models import AccountPreferences
from apps.accounts.throttling import AccountsAppearanceThrottle
from apps.records.models import AuditLog

from .conftest import ME_URL, login, make_client

URL = '/accounts/me/appearance/'
FULL = {'theme': 'indigo-night', 'font': 'nunito', 'density': 'compact', 'text': 'lg', 'contrast': True, 'motion': False}


def as_user(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


@pytest.fixture
def staff(db):
    return User.objects.create_user('ops', password='Sunny-Days-2026', is_staff=True, is_superuser=True)


@pytest.mark.django_db
class TestSave:

    def test_client_saves_and_reads_back(self, verified_client):
        api = as_user(verified_client)
        res = api.put(URL, FULL, format='json')
        assert res.status_code == 200
        assert res.data == FULL
        assert api.get(ME_URL).data['appearance'] == FULL
        assert AccountPreferences.objects.get(user=verified_client).appearance == FULL

    def test_staff_can_save(self, staff):
        res = as_user(staff).put(URL, {'theme': 'electric'}, format='json')
        assert res.status_code == 200
        assert as_user(staff).get(ME_URL).data['appearance']['theme'] == 'electric'

    def test_partial_fills_defaults_then_merges(self, verified_client):
        api = as_user(verified_client)
        first = api.put(URL, {'font': 'plex'}, format='json').data
        assert first == {**DEFAULTS, 'font': 'plex'}
        second = api.put(URL, {'density': 'spacious', 'motion': True}, format='json').data
        assert second == {**DEFAULTS, 'font': 'plex', 'density': 'spacious', 'motion': True}
        assert AccountPreferences.objects.filter(user=verified_client).count() == 1

    def test_unknown_but_well_formed_theme_is_accepted(self, verified_client):
        # The theme list lives in the frontend; it falls back to Default for ids it doesn't know.
        res = as_user(verified_client).put(URL, {'theme': 'brand-new-theme-2'}, format='json')
        assert res.status_code == 200
        assert res.data['theme'] == 'brand-new-theme-2'

    def test_one_account_does_not_touch_another(self, verified_client, unverified_client):
        as_user(verified_client).put(URL, FULL, format='json')
        assert as_user(unverified_client).get(ME_URL).data['appearance'] is None

    def test_not_written_to_the_audit_history(self, verified_client):
        before = AuditLog.objects.count()
        as_user(verified_client).put(URL, FULL, format='json')
        assert AuditLog.objects.count() == before

    def test_requires_sign_in(self, db):
        assert APIClient().put(URL, FULL, format='json').status_code == 401


@pytest.mark.django_db
class TestValidation:

    @pytest.mark.parametrize('body, field', [
        ({'theme': 'Indigo-Night'}, 'theme'),          # uppercase
        ({'theme': 'indigo night'}, 'theme'),          # space
        ({'theme': '-indigo'}, 'theme'),               # leading hyphen
        ({'theme': 'indigo--night'}, 'theme'),         # empty segment
        ({'theme': '<script>'}, 'theme'),
        ({'theme': ''}, 'theme'),
        ({'theme': 'a' * 41}, 'theme'),                # too long
        ({'theme': 7}, 'theme'),
        ({'font': 'comic-sans'}, 'font'),
        ({'density': 'cozy'}, 'density'),
        ({'text': 'xxl'}, 'text'),
        ({'contrast': 'yes'}, 'contrast'),             # booleans must be real true / false
        ({'motion': 1}, 'motion'),
        ({'colour': 'red'}, 'colour'),                 # not an appearance setting
    ])
    def test_rejected(self, verified_client, body, field):
        res = as_user(verified_client).put(URL, body, format='json')
        assert res.status_code == 400, res.data
        assert field in str(res.data)
        assert not AccountPreferences.objects.filter(user=verified_client).exists()

    def test_not_an_object(self, verified_client):
        res = as_user(verified_client).put(URL, ['dark'], format='json')
        assert res.status_code == 400

    def test_invalid_change_keeps_the_saved_value(self, verified_client):
        api = as_user(verified_client)
        api.put(URL, FULL, format='json')
        assert api.put(URL, {'font': 'wingdings'}, format='json').status_code == 400
        assert api.get(ME_URL).data['appearance'] == FULL


@pytest.mark.django_db
class TestReturnedWithAccount:

    def test_me_is_none_before_first_save(self, verified_client):
        assert as_user(verified_client).get(ME_URL).data['appearance'] is None

    def test_sign_in_response_carries_it(self, verified_client):
        assert login('sunco').data['appearance'] is None
        as_user(verified_client).put(URL, FULL, format='json')
        assert login('sunco').data['appearance'] == FULL

    def test_old_saved_value_is_completed_with_defaults(self, verified_client):
        # e.g. saved before a new setting existed, or edited by hand
        AccountPreferences.objects.create(user=verified_client, appearance={'theme': 'solar', 'stray': 1})
        assert as_user(verified_client).get(ME_URL).data['appearance'] == {**DEFAULTS, 'theme': 'solar'}


@pytest.mark.django_db
class TestThrottle:

    def test_rate_limited_per_account(self, monkeypatch, settings):
        assert settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['accounts_appearance'] == '60/m'
        monkeypatch.setattr(AccountsAppearanceThrottle, 'THROTTLE_RATES', {'accounts_appearance': '2/m'})
        one = make_client('alpha', email='a@x.example')
        two = make_client('beta', email='b@x.example')
        api = as_user(one)
        codes = [api.put(URL, {'theme': 'solar'}, format='json').status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        # Another account has its own allowance.
        assert as_user(two).put(URL, {'theme': 'solar'}, format='json').status_code == 200
