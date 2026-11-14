"""
Archiving jobs: the auto rule (done / invoiced + Paid, on create and edit),
its edge cases, archive / unarchive / bulk with the jobs.archive permission,
the active / archived / include-archived lists, counts and summary tiles,
audit entries, trash + restore, and imports that never auto-archive.
"""

import pytest
from django.core.management import call_command

from apps.jobs.models import Job
from apps.records.models import AuditLog

from ..client_portal.conftest import api_for
from .test_jobs import ALL_JOB_PERMS, JOBS, api, boss, create, make_staff  # noqa: F401 (fixtures)
from .test_sheet_import import make_sheet

pytestmark = pytest.mark.django_db


def patch(api, job, **data):
    res = api.patch(f"{JOBS}{job['id']}/", data, format='json')
    assert res.status_code == 200, res.data
    return res.data


def archived(job):
    return Job.objects.get(pk=job['id']).is_archived


def listing(api, **params):
    res = api.get(JOBS, params)
    assert res.status_code == 200
    return res.data


def actions(job):
    return list(AuditLog.objects.filter(object_type='jobs.job', object_id=str(job['id']))
                .order_by('id').values_list('action', 'summary'))


# ── the auto rule ────────────────────────────────────────────────────────────

def test_creating_a_finished_paid_job_archives_it(api, boss):
    job = create(api, status='invoiced', payment='paid')
    assert job['archive_event'] == {'action': 'archived', 'automatic': True, 'note': 'invoiced and paid'}
    assert job['archived_at'] and job['archive_reason'] == 'auto' and job['archive_note'] == 'invoiced and paid'
    assert job['archived_by'] == {'id': boss.pk, 'name': 'boss'}
    assert [a for a, _ in actions(job)] == ['created', 'archived']


@pytest.mark.parametrize('status,payment', [
    ('done', 'no_charge'), ('done', 'unpaid'), ('invoiced', 'deposit'), ('invoiced', ''), ('in_progress', 'paid'),
])
def test_other_combinations_are_not_archived(api, status, payment):
    job = create(api, status=status, payment=payment)
    assert job['archive_event'] is None and not archived(job)


def test_an_edit_that_makes_a_job_done_and_paid_archives_it(api):
    job = create(api, status='done', payment='unpaid')
    res = patch(api, job, payment='paid')
    assert res['archive_event'] == {'action': 'archived', 'automatic': True, 'note': 'done and paid'}
    assert archived(job)
    assert actions(job)[-1] == ('archived', 'Archived automatically: done and paid')


def test_payment_back_to_unpaid_brings_an_auto_archived_job_back(api):
    job = create(api, status='invoiced', payment='paid')
    res = patch(api, job, payment='unpaid')
    assert res['archive_event'] == {'action': 'unarchived', 'automatic': True, 'note': 'payment changed to Unpaid'}
    assert not archived(job) and res['archived_at'] is None
    assert actions(job)[-1] == ('unarchived', 'Unarchived automatically: payment changed to Unpaid')


def test_reopening_brings_an_auto_archived_job_back(api):
    job = create(api, status='done', payment='paid')
    assert patch(api, job, status='in_progress')['archive_event']['note'] == 'status changed to In progress'
    assert not archived(job)


def test_a_job_archived_by_hand_stays_archived_when_it_leaves_the_rule(api):
    job = create(api, status='done', payment='unpaid')
    assert api.post(f"{JOBS}{job['id']}/archive/").status_code == 200
    assert patch(api, job, status='open')['archive_event'] is None
    assert archived(job)


def test_undo_keeps_the_job_active_until_it_is_paid_again(api):
    job = create(api, status='invoiced', payment='paid')
    assert api.post(f"{JOBS}{job['id']}/unarchive/").status_code == 200  # Undo
    assert patch(api, job, task='Typo fixed')['archive_event'] is None
    assert patch(api, job, status='done')['archive_event'] is None  # still inside the rule
    assert not archived(job)
    patch(api, job, payment='unpaid')
    assert patch(api, job, payment='paid')['archive_event']['action'] == 'archived'


def test_comments_dont_bring_an_archived_job_back(api):
    job = create(api, status='done', payment='paid')
    assert api.post(f"{JOBS}{job['id']}/comments/", {'body': 'Client says thanks'}, format='json').status_code == 201
    assert archived(job)


def test_archiving_does_not_move_last_updated(api):
    job = create(api)
    before = Job.objects.get(pk=job['id']).last_updated
    api.post(f"{JOBS}{job['id']}/archive/")
    assert Job.objects.get(pk=job['id']).last_updated == before


def test_the_sheet_import_never_archives(tmp_path):
    call_command('import_jobs_sheet', make_sheet(tmp_path / 'ops.xlsx'))
    one = Job.objects.get(number=1)
    assert (one.status, one.payment) == ('invoiced', 'paid') and not one.is_archived


# ── archive / unarchive / bulk ───────────────────────────────────────────────

def test_archive_and_unarchive_by_hand(api, boss):
    job = create(api)
    res = api.post(f"{JOBS}{job['id']}/archive/")
    assert res.status_code == 200
    assert res.data['archive_reason'] == 'manual' and res.data['archived_by']['id'] == boss.pk
    assert api.post(f"{JOBS}{job['id']}/archive/").status_code == 200  # already archived: fine
    assert api.post(f"{JOBS}{job['id']}/unarchive/").data['archived_at'] is None
    assert [a for a, _ in actions(job)] == ['created', 'archived', 'unarchived']
    assert api.get(f"{JOBS}{job['id']}/archive/").status_code == 405


def test_archive_needs_the_permission(api):
    job = create(api)
    reader = api_for(make_staff('reader', perms=[p for p in ALL_JOB_PERMS]))
    res = reader.post(f"{JOBS}{job['id']}/archive/")
    assert res.status_code == 403 and 'archive' in res.data['detail'].lower()
    assert reader.post(f'{JOBS}bulk-archive/', {'ids': [job['id']], 'archived': True}, format='json').status_code == 403
    archiver = api_for(make_staff('archiver', perms=['jobs.view', 'jobs.archive']))
    assert archiver.post(f"{JOBS}{job['id']}/archive/").status_code == 200
    assert archiver.post(f"{JOBS}{job['id']}/unarchive/").status_code == 200


def test_auto_archive_needs_no_archive_permission(api):
    job = create(api, status='done', payment='unpaid')
    editor = api_for(make_staff('editor', perms=['jobs.view', 'jobs.edit']))
    res = editor.patch(f"{JOBS}{job['id']}/", {'payment': 'paid'}, format='json')
    assert res.status_code == 200 and res.data['archive_event']['action'] == 'archived'


def test_bulk_archive_and_unarchive(api):
    a, b, c = create(api), create(api), create(api)
    api.post(f"{JOBS}{c['id']}/archive/")
    res = api.post(f'{JOBS}bulk-archive/', {'ids': [a['id'], b['id'], c['id'], 999999], 'archived': True},
                   format='json')
    assert res.status_code == 200
    assert res.data == {'done': [a['id'], b['id']], 'skipped': [c['id'], 999999]}
    assert all(archived(j) for j in (a, b, c))
    res = api.post(f'{JOBS}bulk-archive/', {'ids': [a['id']], 'archived': False}, format='json')
    assert res.data == {'done': [a['id']], 'skipped': []} and not archived(a)


@pytest.mark.parametrize('body', [{}, {'ids': 'x', 'archived': True}, {'ids': [1], 'archived': 'yes'},
                                  {'ids': list(range(201)), 'archived': True}])
def test_bulk_archive_rejects_bad_requests(api, body):
    assert api.post(f'{JOBS}bulk-archive/', body, format='json').status_code == 400


def test_trashed_jobs_cant_be_archived_and_restore_keeps_the_archive(api):
    job = create(api, status='done', payment='paid')
    assert api.delete(f"{JOBS}{job['id']}/").status_code == 204
    assert api.post(f"{JOBS}{job['id']}/unarchive/").status_code == 404
    assert api.post(f"/records/trash/jobs.job/{job['id']}/restore/").status_code == 200
    back = Job.objects.get(pk=job['id'])
    assert back.is_archived and back.archive_reason == 'auto' and back.archive_note == 'done and paid'


# ── lists, counts and tiles ──────────────────────────────────────────────────

def test_lists_show_active_jobs_unless_asked(api):
    live = create(api, client_name='Live Co', status='open')
    old = create(api, client_name='Old Co', status='invoiced', payment='paid')
    assert [r['id'] for r in listing(api)['results']] == [live['id']]
    assert [r['id'] for r in listing(api, archived=1)['results']] == [old['id']]
    assert {r['id'] for r in listing(api, include_archived=1)['results']} == {live['id'], old['id']}
    found = listing(api, q='Old Co', include_archived=1)['results']
    assert [(r['id'], bool(r['archived_at'])) for r in found] == [(old['id'], True)]
    assert listing(api, q='Old Co')['results'] == []


def test_counts_and_tiles_follow_the_view(api):
    create(api, status='open')
    create(api, status='done', payment='unpaid')  # awaiting payment
    create(api, status='invoiced', payment='paid')
    done = create(api, status='done', payment='unpaid')
    api.post(f"{JOBS}{done['id']}/archive/")  # archived while still unpaid

    active = listing(api)
    assert active['counts']['all'] == 2 and active['counts']['invoiced'] == 0
    assert active['summary']['awaiting_payment'] == 1 and active['summary']['unassigned'] == 1
    assert active['archived_count'] == 2

    arch = listing(api, archived=1)
    assert arch['counts']['all'] == 2 and arch['counts']['invoiced'] == 1 and arch['counts']['done'] == 1
    assert arch['summary']['awaiting_payment'] == 1 and arch['summary']['unassigned'] == 0
    assert arch['archived_count'] == 2
    assert listing(api, archived=1, status='done')['count'] == 1
