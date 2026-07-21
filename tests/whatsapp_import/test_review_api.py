"""
Tests for the WhatsApp message review API (apps.whatsapp_import.views).

Admin-only listing/filtering + per-message and bulk job marking, including the
guard that won't unmark a message already exported to the jobs sheet.
"""

from datetime import datetime, timezone as dt_timezone

import pytest

from apps.core.models import ClientProfile
from apps.whatsapp_import.models import WhatsAppMessage


def _msg(sender, text, chat='House Brown', **kw):
    return WhatsAppMessage.objects.create(
        sender=sender, text=text, chat_name=chat,
        sent_at=datetime(2026, 7, 10, 8, 0, tzinfo=dt_timezone.utc), **kw,
    )


@pytest.fixture
def client_user(db, user_factory):
    """A non-admin user (has a ClientProfile role=client)."""
    u = user_factory(username='clientguy')
    ClientProfile.objects.create(user=u, role='client', company_name='Acme')
    return u


@pytest.mark.django_db
class TestReviewAPI:

    def test_list_requires_auth(self, api_client):
        assert api_client.get('/whatsapp/messages/').status_code == 401

    def test_list_forbidden_for_client(self, api_client, client_user):
        api_client.force_authenticate(user=client_user)
        _msg('Brian', 'hi')
        assert api_client.get('/whatsapp/messages/').status_code == 403

    def test_admin_lists_messages(self, admin_api_client):
        _msg('Brian', 'site visit done')
        _msg('Office', 'need inverter')
        res = admin_api_client.get('/whatsapp/messages/')
        assert res.status_code == 200
        assert res.data['count'] == 2

    def test_filter_by_chat(self, admin_api_client):
        _msg('Brian', 'a', chat='House Brown')
        _msg('Sipho', 'b', chat='House Nash')
        res = admin_api_client.get('/whatsapp/messages/?chat=House Nash')
        assert res.data['count'] == 1
        assert res.data['results'][0]['sender'] == 'Sipho'

    def test_filter_by_status_and_search(self, admin_api_client):
        m1 = _msg('Brian', 'need a new inverter')
        _msg('Office', 'ok thanks')
        m1.marked_as_job = True
        m1.save()
        assert admin_api_client.get('/whatsapp/messages/?status=marked').data['count'] == 1
        assert admin_api_client.get('/whatsapp/messages/?status=unmarked').data['count'] == 1
        # search across all statuses (m1 is marked, so must use status=all)
        assert admin_api_client.get('/whatsapp/messages/?search=inverter&status=all').data['count'] == 1

    def test_chat_list(self, admin_api_client):
        _msg('Brian', 'a', chat='House Brown')
        _msg('Brian', 'b', chat='House Brown')
        _msg('Sipho', 'c', chat='House Nash')
        res = admin_api_client.get('/whatsapp/chats/')
        assert res.status_code == 200
        names = {c['chat_name']: c['count'] for c in res.data}
        assert names == {'House Brown': 2, 'House Nash': 1}

    def test_mark_single_message(self, admin_api_client):
        m = _msg('Brian', 'job here')
        res = admin_api_client.patch(f'/whatsapp/messages/{m.id}/mark/',
                                     {'marked_as_job': True}, format='json')
        assert res.status_code == 200
        assert res.data['marked_as_job'] is True
        m.refresh_from_db()
        assert m.marked_as_job and m.marked_as_job_at is not None

    def test_unmark_single_message(self, admin_api_client):
        m = _msg('Brian', 'x', marked_as_job=True)
        res = admin_api_client.patch(f'/whatsapp/messages/{m.id}/mark/',
                                     {'marked_as_job': False}, format='json')
        assert res.status_code == 200
        m.refresh_from_db()
        assert m.marked_as_job is False
        assert m.marked_as_job_at is None

    def test_cannot_unmark_exported_message(self, admin_api_client):
        m = _msg('Brian', 'x', marked_as_job=True, exported_to_jobs_sheet=True)
        admin_api_client.patch(f'/whatsapp/messages/{m.id}/mark/',
                               {'marked_as_job': False}, format='json')
        m.refresh_from_db()
        # Still marked — exported jobs can't be un-marked.
        assert m.marked_as_job is True

    def test_bulk_mark(self, admin_api_client):
        a, b, c = _msg('X', '1'), _msg('X', '2'), _msg('X', '3')
        res = admin_api_client.post('/whatsapp/messages/bulk-mark/',
                                    {'ids': [a.id, b.id, c.id], 'marked_as_job': True},
                                    format='json')
        assert res.status_code == 200
        assert res.data['updated'] == 3
        assert WhatsAppMessage.objects.filter(marked_as_job=True).count() == 3

    def test_bulk_unmark_skips_exported(self, admin_api_client):
        a = _msg('X', '1', marked_as_job=True)
        b = _msg('X', '2', marked_as_job=True, exported_to_jobs_sheet=True)
        res = admin_api_client.post('/whatsapp/messages/bulk-mark/',
                                    {'ids': [a.id, b.id], 'marked_as_job': False},
                                    format='json')
        # Only the non-exported one flips.
        assert res.data['updated'] == 1
        a.refresh_from_db(); b.refresh_from_db()
        assert a.marked_as_job is False
        assert b.marked_as_job is True

    def test_bulk_requires_ids(self, admin_api_client):
        res = admin_api_client.post('/whatsapp/messages/bulk-mark/',
                                    {'ids': [], 'marked_as_job': True}, format='json')
        assert res.status_code == 400


@pytest.mark.django_db
class TestDismiss:
    """The 'not a job' dismissal — hides from default view, reversible."""

    def test_default_view_hides_dismissed(self, admin_api_client):
        _msg('A', 'keep me')
        _msg('B', 'noise', dismissed=True, dismissed_at=datetime(2026, 7, 10, 9, 0, tzinfo=dt_timezone.utc))
        # No status param -> defaults to 'unmarked' (not job, not dismissed).
        res = admin_api_client.get('/whatsapp/messages/')
        assert res.data['count'] == 1
        assert res.data['results'][0]['text'] == 'keep me'

    def test_dismissed_filter_shows_only_dismissed(self, admin_api_client):
        _msg('A', 'keep me')
        _msg('B', 'noise', dismissed=True)
        res = admin_api_client.get('/whatsapp/messages/?status=dismissed')
        assert res.data['count'] == 1
        assert res.data['results'][0]['text'] == 'noise'

    def test_all_includes_dismissed(self, admin_api_client):
        _msg('A', 'keep me')
        _msg('B', 'noise', dismissed=True)
        res = admin_api_client.get('/whatsapp/messages/?status=all')
        assert res.data['count'] == 2

    def test_marked_view_excludes_dismissed(self, admin_api_client):
        _msg('A', 'a job', marked_as_job=True)
        _msg('B', 'dismissed job', marked_as_job=True, dismissed=True)
        res = admin_api_client.get('/whatsapp/messages/?status=marked')
        assert res.data['count'] == 1
        assert res.data['results'][0]['text'] == 'a job'

    def test_dismiss_single(self, admin_api_client):
        m = _msg('A', 'noise')
        res = admin_api_client.patch(f'/whatsapp/messages/{m.id}/dismiss/',
                                     {'dismissed': True}, format='json')
        assert res.status_code == 200
        assert res.data['dismissed'] is True
        m.refresh_from_db()
        assert m.dismissed and m.dismissed_at is not None

    def test_undismiss_single(self, admin_api_client):
        m = _msg('A', 'noise', dismissed=True)
        res = admin_api_client.patch(f'/whatsapp/messages/{m.id}/dismiss/',
                                     {'dismissed': False}, format='json')
        assert res.status_code == 200
        m.refresh_from_db()
        assert m.dismissed is False and m.dismissed_at is None

    def test_dismiss_clears_job_flag(self, admin_api_client):
        m = _msg('A', 'was a job', marked_as_job=True)
        admin_api_client.patch(f'/whatsapp/messages/{m.id}/dismiss/',
                               {'dismissed': True}, format='json')
        m.refresh_from_db()
        assert m.dismissed is True
        assert m.marked_as_job is False   # dismissing implies not-a-job

    def test_dismiss_wont_clear_exported_job(self, admin_api_client):
        m = _msg('A', 'exported job', marked_as_job=True, exported_to_jobs_sheet=True)
        admin_api_client.patch(f'/whatsapp/messages/{m.id}/dismiss/',
                               {'dismissed': True}, format='json')
        m.refresh_from_db()
        # Dismissed flag set, but the exported job flag is protected.
        assert m.dismissed is True
        assert m.marked_as_job is True

    def test_bulk_dismiss(self, admin_api_client):
        a, b = _msg('A', '1'), _msg('B', '2')
        res = admin_api_client.post('/whatsapp/messages/bulk-dismiss/',
                                    {'ids': [a.id, b.id], 'dismissed': True}, format='json')
        assert res.data['updated'] == 2
        assert WhatsAppMessage.objects.filter(dismissed=True).count() == 2

    def test_bulk_undismiss(self, admin_api_client):
        a = _msg('A', '1', dismissed=True)
        res = admin_api_client.post('/whatsapp/messages/bulk-dismiss/',
                                    {'ids': [a.id], 'dismissed': False}, format='json')
        assert res.data['updated'] == 1
        a.refresh_from_db()
        assert a.dismissed is False

    def test_dismiss_forbidden_for_client(self, api_client, client_user):
        m = _msg('A', 'x')
        api_client.force_authenticate(user=client_user)
        assert api_client.patch(f'/whatsapp/messages/{m.id}/dismiss/',
                                {'dismissed': True}, format='json').status_code == 403


@pytest.mark.django_db
class TestStatusCounts:
    def test_counts_by_status(self, admin_api_client):
        _msg('A', 'to review 1')
        _msg('B', 'to review 2')
        _msg('C', 'a job', marked_as_job=True)
        _msg('D', 'noise', dismissed=True)
        res = admin_api_client.get('/whatsapp/counts/')
        assert res.status_code == 200
        assert res.data == {'unmarked': 2, 'marked': 1, 'dismissed': 1, 'all': 4}

    def test_counts_respect_chat_filter(self, admin_api_client):
        _msg('A', 'x', chat='House Brown')
        _msg('B', 'y', chat='House Nash')
        res = admin_api_client.get('/whatsapp/counts/?chat=House Nash')
        assert res.data['all'] == 1

    def test_counts_forbidden_for_client(self, api_client, client_user):
        api_client.force_authenticate(user=client_user)
        assert api_client.get('/whatsapp/counts/').status_code == 403
