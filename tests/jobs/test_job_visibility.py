"""
Who sees which job (apps.jobs.visibility): staff with jobs.view ("see all jobs")
see every job as before; every other staff member can open Jobs but only sees
the jobs assigned to them or created by them. Their other job permissions apply
only to those. A job they can't see is a 404 everywhere and never leaks through
another endpoint (list, search, counts, tiles, detail, edit, delete, archive,
comments, history, trash, tool bookings, WhatsApp review).
"""

import importlib
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.core.models import ClientProfile
from apps.jobs.models import Job, JobCategory
from apps.jobs.services import save_with_next_number
from apps.staff.models import StaffAccess
from apps.tools.models import Booking, Tool, ToolType
from apps.whatsapp_import.models import WhatsAppMessage
from tests.client_portal.conftest import api_for

pytestmark = pytest.mark.django_db
JOBS = '/jobs/'
ALL_BUT_VIEW = ['jobs.create', 'jobs.edit', 'jobs.assign', 'jobs.delete', 'jobs.archive']


@pytest.fixture(autouse=True)
def seeds(db):
    from django.apps import apps
    importlib.import_module('apps.jobs.migrations.0002_seed_categories').seed(apps, None)
    importlib.import_module('apps.tools.migrations.0002_seed_types').seed(apps, None)


def make_staff(username, perms, first_name=''):
    user = User.objects.create_user(username, password='x', first_name=first_name or username.title())
    ClientProfile.objects.create(user=user, role='admin')
    StaffAccess.objects.create(user=user, permissions=perms)
    return user


def new_job(created_by, assigned=(), **kw):
    job = Job(category=JobCategory.objects.get(name='Call out'), created_by=created_by,
              client_name=kw.pop('client_name', 'Acme'), task=kw.pop('task', 'Fault'), **kw)
    save_with_next_number(job)
    job.assigned_to.set(assigned)
    return job


@pytest.fixture
def world(admin_user):
    boss = admin_user
    lucky = make_staff('lucky', ALL_BUT_VIEW + ['tools.view', 'tools.book', 'whatsapp.view', 'trash.view'])
    other = make_staff('other', [])
    today = timezone.localdate()
    return {
        'boss': boss, 'lucky': lucky, 'other': other,
        # Assigned to Lucky (created by the boss): visible to Lucky.
        'assigned': new_job(boss, [lucky], client_name='Mine Assigned', scheduled_date=today),
        # Created by Lucky, nobody on it: visible to Lucky.
        'created': new_job(lucky, [], client_name='Mine Created'),
        # Somebody else's: never visible to Lucky.
        'secret': new_job(boss, [other], client_name='Secret Client', task='Top secret', scheduled_date=today,
                          last_updated=timezone.now() - timedelta(days=30)),
    }


def ids(res):
    return sorted(r['id'] for r in res.data['results'])


# ── the list ────────────────────────────────────────────────────────────────

def test_list_shows_only_their_jobs(world):
    lucky = api_for(world['lucky'])
    res = lucky.get(JOBS)
    assert res.status_code == 200
    assert ids(res) == sorted([world['assigned'].pk, world['created'].pk])
    assert res.data['count'] == 2 and res.data['counts']['all'] == 2
    assert res.data['sees_all'] is False
    # Search, number lookup and every filter stay inside their jobs.
    for params in ({'q': 'Secret'}, {'q': 'top secret'}, {'q': world['secret'].job_id},
                   {'assigned': world['other'].pk}, {'attention': 1}, {'visit': 'today'}):
        found = lucky.get(JOBS, params)
        assert world['secret'].pk not in [r['id'] for r in found.data['results']], params
    assert ids(lucky.get(JOBS, {'visit': 'today'})) == [world['assigned'].pk]
    assert ids(lucky.get(JOBS, {'unassigned': 1})) == [world['created'].pk]


def test_tiles_and_archive_counts_are_their_own(world):
    lucky = api_for(world['lucky'])
    summary = lucky.get(JOBS).data['summary']
    assert summary['visits_today'] == 1      # not the secret job's visit
    assert summary['attention'] == 0         # the secret job is 30 days stale
    assert summary['unassigned'] == 1
    Job.objects.filter(pk=world['secret'].pk).update(archived_at=timezone.now())
    assert lucky.get(JOBS).data['archived_count'] == 0
    assert lucky.get(JOBS, {'archived': 1}).data['count'] == 0


def test_staff_with_jobs_view_see_everything_as_before(world):
    viewer = api_for(make_staff('viewer', ['jobs.view']))
    res = viewer.get(JOBS)
    assert res.data['count'] == 3 and res.data['sees_all'] is True
    assert res.data['summary']['attention'] == 1
    assert viewer.get(f"{JOBS}{world['secret'].pk}/").status_code == 200


def test_staff_with_no_permissions_open_jobs_and_see_their_own(world):
    api = api_for(world['other'])
    assert ids(api.get(JOBS)) == [world['secret'].pk]  # Other is on it


# ── one job: every endpoint answers 404 for a job they can't see ─────────────

def test_no_endpoint_reaches_someone_elses_job(world):
    lucky = api_for(world['lucky'])
    secret = world['secret']
    url = f'{JOBS}{secret.pk}/'
    assert lucky.get(url).status_code == 404
    assert lucky.patch(url, {'task': 'hacked'}, format='json').status_code == 404
    assert lucky.patch(url, {'assigned_to': [world['lucky'].pk]}, format='json').status_code == 404
    assert lucky.post(f'{url}archive/').status_code == 404
    assert lucky.post(f'{url}unarchive/').status_code == 404
    assert lucky.post(f'{url}comments/', {'body': 'hi'}, format='json').status_code == 404
    assert lucky.get(f'/records/history/jobs.job/{secret.pk}/').status_code == 404
    assert lucky.delete(url).status_code == 404
    res = lucky.post(f'{JOBS}bulk-archive/', {'ids': [world['assigned'].pk, secret.pk], 'archived': True},
                     format='json')
    assert res.data == {'done': [world['assigned'].pk], 'skipped': [secret.pk]}
    secret.refresh_from_db()
    assert secret.task == 'Top secret' and secret.archived_at is None and not secret.in_trash
    assert list(secret.assigned_to.all()) == [world['other']]


def test_own_job_history_is_fine(world):
    api = api_for(world['lucky'])
    assert api.get(f"/records/history/jobs.job/{world['created'].pk}/").status_code == 200


def test_comment_edit_on_someone_elses_job_is_404(world):
    boss = api_for(world['boss'])
    comment = boss.post(f"{JOBS}{world['secret'].pk}/comments/", {'body': 'Boss note'}, format='json').data
    lucky = api_for(world['lucky'])
    url = f"{JOBS}{world['secret'].pk}/comments/{comment['id']}/"
    assert lucky.patch(url, {'body': 'x'}, format='json').status_code == 404


def test_trash_never_shows_someone_elses_job(world):
    api_for(world['boss']).delete(f"{JOBS}{world['secret'].pk}/")
    lucky = api_for(world['lucky'])
    assert lucky.get('/records/trash/').data['results'] == []
    assert lucky.get(f"/records/trash/jobs.job/{world['secret'].pk}/").status_code == 404
    assert lucky.post(f"/records/trash/jobs.job/{world['secret'].pk}/restore/").status_code == 404


def test_they_find_their_own_deleted_job_in_the_trash(world):
    lucky = api_for(world['lucky'])
    assert lucky.delete(f"{JOBS}{world['created'].pk}/").status_code == 204
    assert [t['name'] for t in lucky.get('/records/trash/').data['results']] == [
        f"{world['created'].job_id} · Mine Created"]
    assert lucky.post(f"/records/trash/jobs.job/{world['created'].pk}/restore/").status_code == 200


# ── tool bookings and WhatsApp: the number, no link, no lookup ──────────────

def booking_for(job, technician, by):
    tool = Tool.objects.create(name='Drill', type=ToolType.objects.first(), number=1)
    booking = Booking.objects.create(number=1, technician=technician, date_out=timezone.localdate(), job=job,
                                     created_by=by)
    booking.items.create(tool=tool, tool_label='Drill', quantity=1)
    return booking


def test_tool_booking_shows_the_number_without_a_link(world):
    booking = booking_for(world['secret'], world['lucky'], world['boss'])
    lucky = api_for(world['lucky'])
    detail = lucky.get(f'/tools/bookings/{booking.pk}/').data
    assert detail['job'] == {'id': world['secret'].pk, 'job_id': world['secret'].job_id, 'can_open': False}
    row = lucky.get('/tools/bookings/').data['results'][0]
    assert row['job']['can_open'] is False
    tool = lucky.get(f"/tools/{booking.items.get().tool_id}/").data
    assert tool['bookings'][0]['job']['can_open'] is False
    # Their own job links.
    Booking.objects.filter(pk=booking.pk).update(job=world['assigned'])
    assert lucky.get(f'/tools/bookings/{booking.pk}/').data['job']['can_open'] is True


def test_tool_booking_job_lookup_only_finds_their_jobs(world):
    tool = Tool.objects.create(name='Ladder', type=ToolType.objects.first(), number=7)
    lucky = api_for(world['lucky'])
    body = {'technician': world['lucky'].pk, 'items': [{'tool': tool.pk, 'quantity': 1}],
            'date_out': str(timezone.localdate()), 'notify': False}
    res = lucky.post('/tools/bookings/', {**body, 'job': world['secret'].job_id}, format='json')
    missing = lucky.post('/tools/bookings/', {**body, 'job': 'APS-999'}, format='json')
    assert res.status_code == missing.status_code == 400
    # Same answer as a job that doesn't exist: no way to probe for it.
    assert res.data['job'][0].replace(world['secret'].job_id, 'X') == missing.data['job'][0].replace('APS-999', 'X')
    ok = lucky.post('/tools/bookings/', {**body, 'job': world['assigned'].job_id}, format='json')
    assert ok.status_code == 201 and ok.data['job']['can_open'] is True


def test_editing_a_booking_keeps_a_job_they_cant_see(world):
    booking = booking_for(world['secret'], world['lucky'], world['boss'])
    StaffAccess.objects.filter(user=world['lucky']).update(
        permissions=ALL_BUT_VIEW + ['tools.view', 'tools.book', 'tools.edit', 'trash.view'])
    lucky = api_for(world['lucky'])
    res = lucky.patch(f'/tools/bookings/{booking.pk}/', {'job': world['secret'].job_id, 'notes': 'x'},
                      format='json')
    assert res.status_code == 200, res.data
    booking.refresh_from_db()
    assert booking.job_id == world['secret'].pk


def test_whatsapp_shows_the_number_without_a_link(world):
    from datetime import datetime, timezone as tz
    WhatsAppMessage.objects.create(sender='B', text='need help', chat_name='House', job=world['secret'],
                                   marked_as_job=True, sent_at=datetime(2026, 7, 10, 8, tzinfo=tz.utc))
    lucky = api_for(world['lucky'])
    row = lucky.get('/whatsapp/messages/', {'status': 'marked'}).data['results'][0]
    assert row['job'] == {'id': world['secret'].pk, 'job_id': world['secret'].job_id, 'can_open': False}


# ── their other permissions, only on their jobs ─────────────────────────────

def test_permissions_still_apply_to_their_own_jobs(world):
    plain = make_staff('plain', [])
    job = new_job(world['boss'], [plain])
    api = api_for(plain)
    url = f'{JOBS}{job.pk}/'
    assert api.get(url).status_code == 200
    assert api.patch(url, {'task': 'x'}, format='json').status_code == 403
    assert api.delete(url).status_code == 403
    assert api.post(f'{url}archive/').status_code == 403
    assert api.post(JOBS, {'client_name': 'New', 'category': job.category_id}, format='json').status_code == 403


def test_a_job_they_create_is_theirs(world):
    lucky = api_for(world['lucky'])
    res = lucky.post(JOBS, {'client_name': 'Fresh', 'category': JobCategory.objects.first().pk}, format='json')
    assert res.status_code == 201
    assert res.data['id'] in ids(lucky.get(JOBS))


def test_taking_yourself_off_a_job(world):
    lucky = api_for(world['lucky'])
    url = f"{JOBS}{world['assigned'].pk}/"
    res = lucky.patch(url, {'assigned_to': []}, format='json')
    assert res.status_code == 200
    assert res.data['no_longer_visible'] is True and res.data['job_id'] == world['assigned'].job_id
    assert 'client_name' not in res.data
    assert lucky.get(url).status_code == 404


# ── comments ────────────────────────────────────────────────────────────────

def test_assigned_and_creator_can_comment_without_the_permission(world):
    lucky = api_for(world['lucky'])  # no jobs.comment
    for job in (world['assigned'], world['created']):
        url = f'{JOBS}{job.pk}/'
        assert lucky.get(url).data['can_comment'] is True
        res = lucky.post(f'{url}comments/', {'body': 'On my way'}, format='json')
        assert res.status_code == 201
        assert lucky.patch(f"{url}comments/{res.data['id']}/", {'body': 'Arrived'}, format='json').status_code == 200


def test_seeing_a_job_isnt_enough_to_comment(world):
    viewer = api_for(make_staff('viewer', ['jobs.view']))
    url = f"{JOBS}{world['secret'].pk}/"
    assert viewer.get(url).data['can_comment'] is False
    assert viewer.post(f'{url}comments/', {'body': 'x'}, format='json').status_code == 403
    commenter = api_for(make_staff('commenter', ['jobs.view', 'jobs.comment']))
    assert commenter.get(url).data['can_comment'] is True
    assert commenter.post(f'{url}comments/', {'body': 'x'}, format='json').status_code == 201


# ── accounts: new ones see their own jobs, existing ones keep what they had ──

def test_new_staff_account_starts_without_see_all_jobs(world, admin_user):
    res = api_for(admin_user).post('/staff/accounts/', {
        'username': 'newbie', 'name': 'New Bie', 'password': 'Sunny-Days-2026!x',
        'confirm_password': 'Sunny-Days-2026!x'}, format='json')
    assert res.status_code == 201
    assert 'jobs.view' not in res.data['permissions']
    newbie = User.objects.get(username='newbie')
    assert api_for(newbie).get(JOBS).data['count'] == 0


def test_older_account_without_a_permissions_row_still_sees_all(world):
    old = User.objects.create_user('oldadmin', password='x')
    ClientProfile.objects.create(user=old, role='admin')  # no StaffAccess row
    assert api_for(old).get(JOBS).data['count'] == 3


def test_clients_never_see_jobs(world):
    from tests.client_portal.conftest import make_client
    client = api_for(make_client('acme'))
    assert client.get(JOBS).status_code == 403
    assert client.get(f"{JOBS}{world['assigned'].pk}/").status_code == 403
