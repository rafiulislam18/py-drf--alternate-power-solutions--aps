"""
Operations jobs (apps.jobs) — the dashboard's replacement for the Jobs tab of
the APS Operations sheet: APS-### numbers, the sheet's status rules, "last
updated" moving on any change, age colours, comments, trash, audit history
and per-action permissions.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.core.models import ClientProfile
from apps.jobs.models import Job, JobCategory, JobComment, JobSettings
from apps.records.models import AuditLog
from apps.solar_dashboard.models import Site
from apps.staff.models import StaffAccess

from ..client_portal.conftest import api_for, make_client

pytestmark = pytest.mark.django_db
JOBS = '/jobs/'
ALL_JOB_PERMS = ['jobs.view', 'jobs.create', 'jobs.edit', 'jobs.assign', 'jobs.comment', 'jobs.delete',
                 'jobs.settings', 'trash.view']


def make_staff(username, perms=None, first_name=''):
    user = User.objects.create_user(username, password='x', first_name=first_name)
    ClientProfile.objects.create(user=user, role='admin')
    if perms is not None:
        StaffAccess.objects.create(user=user, permissions=perms)
    return user


@pytest.fixture
def boss(db):
    user = User.objects.create_user('boss', password='x', is_staff=True, is_superuser=True)
    ClientProfile.objects.create(user=user, role='admin')
    return user


@pytest.fixture
def api(boss):
    return api_for(boss)


def cat(name):
    return JobCategory.objects.get(name=name).pk


def create(api, **data):
    data.setdefault('category', cat('Call out'))
    res = api.post(JOBS, {'client_name': 'Acme', 'task': 'Inverter fault', **data}, format='json')
    assert res.status_code == 201, res.data
    return res.data


def age(job_id, days):
    Job.objects.filter(pk=job_id).update(last_updated=timezone.now() - timedelta(days=days))


# ── numbering ────────────────────────────────────────────────────────────────

def test_jobs_are_numbered_in_order_and_trashed_numbers_stay_taken(api, boss):
    a, b = create(api), create(api, client_name='Beta')
    assert (a['job_id'], b['job_id']) == ('APS-001', 'APS-002')
    assert api.delete(f"{JOBS}{b['id']}/").status_code == 204
    assert create(api)['job_id'] == 'APS-003'  # APS-002 is in the trash, never reused
    assert api.get(f'{JOBS}options/').data['next_job_id'] == 'APS-004'


def test_a_job_needs_a_client_or_a_task(api):
    res = api.post(JOBS, {'client_name': ' ', 'task': '', 'category': cat('Other')}, format='json')
    assert res.status_code == 400 and 'client_name' in res.data
    res = api.post(JOBS, {'task': 'Fix gate motor'}, format='json')
    assert res.status_code == 400 and 'category' in res.data
    assert api.post(JOBS, {'task': 'Fix gate motor', 'category': cat('Other')}, format='json').status_code == 201


# ── the sheet's status rules ─────────────────────────────────────────────────

def test_done_stamps_the_completion_date_and_reopening_clears_it(api):
    job = create(api)
    url = f"{JOBS}{job['id']}/"
    done = api.patch(url, {'status': 'done'}, format='json').data
    assert done['completion_date'] == timezone.localdate()

    # DONE → INVOICED keeps the first completion date (and its unpaid alert record).
    Job.objects.filter(pk=job['id']).update(completion_date=timezone.localdate() - timedelta(days=3),
                                            unpaid_alert_at=timezone.now())
    invoiced = api.patch(url, {'status': 'invoiced'}, format='json').data
    assert invoiced['completion_date'] == timezone.localdate() - timedelta(days=3)
    assert Job.objects.get(pk=job['id']).unpaid_alert_at is not None

    reopened = api.patch(url, {'status': 'in_progress'}, format='json').data
    assert reopened['completion_date'] is None
    assert Job.objects.get(pk=job['id']).unpaid_alert_at is None


def test_creating_a_finished_job_stamps_today(api):
    assert create(api, status='done')['completion_date'] == timezone.localdate()


# ── last updated ─────────────────────────────────────────────────────────────

def test_any_change_moves_last_updated_but_a_save_that_changes_nothing_does_not(api, boss):
    job = create(api, invoice='INV-02414')
    url = f"{JOBS}{job['id']}/"
    for change in ({'invoice': 'No charge'}, {'category': cat('MLP')}, {'call_date': '2026-11-01'},
                   {'scheduled_time': '10h00 - 10h30'}, {'payment': 'deposit'}, {'assigned_to': [boss.pk]}):
        age(job['id'], 10)
        assert api.patch(url, change, format='json').status_code == 200
        assert Job.objects.get(pk=job['id']).last_updated > timezone.now() - timedelta(minutes=1), change

    age(job['id'], 10)
    api.patch(url, {'invoice': 'No charge', 'assigned_to': [boss.pk]}, format='json')  # same values
    assert Job.objects.get(pk=job['id']).last_updated < timezone.now() - timedelta(days=9)

    api.post(f'{url}comments/', {'body': 'Called the client'}, format='json')
    assert Job.objects.get(pk=job['id']).last_updated > timezone.now() - timedelta(minutes=1)


# ── list: order, filters, colours ────────────────────────────────────────────

def test_list_order_filters_counts_and_search(api, boss):
    done = create(api, client_name='Done Co', status='done')
    open_old = create(api, client_name='Old Open')
    progress = create(api, client_name='Busy', status='in_progress', assigned_to=[boss.pk])
    Job.objects.filter(pk=open_old['id']).update(date_added=timezone.now() - timedelta(days=30))
    newest_open = create(api, client_name='New Open', category=cat('Maintenance'))

    res = api.get(JOBS).data
    assert [r['client_name'] for r in res['results']] == ['New Open', 'Old Open', 'Busy', 'Done Co']
    assert res['counts']['all'] == 4 and res['counts']['open'] == 2 and res['counts']['done'] == 1

    def names(**params):
        return [r['client_name'] for r in api.get(JOBS, params).data['results']]

    assert names(status='open,done') == ['New Open', 'Old Open', 'Done Co']
    assert names(category=cat('Maintenance')) == ['New Open']
    assert names(assigned='me') == ['Busy']
    assert set(names(assigned='none')) == {'New Open', 'Old Open', 'Done Co'}
    assert names(q=done['job_id']) == ['Done Co']
    Job.objects.filter(pk=done['id']).update(invoice='INV-02414')
    assert names(q='inv-024') == ['Done Co']
    assert names(q='aps-3') == ['Busy']
    assert progress['assigned_to'] == [{'id': boss.pk, 'name': 'boss'}]
    assert newest_open['job_id'] == 'APS-004'


def test_live_jobs_are_coloured_by_days_since_the_last_update(api):
    jobs = {name: create(api, client_name=name, status=status)
            for name, status in [('fresh', 'open'), ('watch', 'open'), ('aging', 'quote_sent'),
                                 ('critical', 'in_progress'), ('parked', 'on_hold'), ('done', 'done')]}
    for name, days in [('watch', 7), ('aging', 14), ('critical', 21), ('parked', 40), ('done', 40)]:
        age(jobs[name]['id'], days)
    rows = {r['client_name']: r for r in api.get(JOBS).data['results']}
    assert {n: rows[n]['severity'] for n in rows} == \
        {'fresh': 0, 'watch': 1, 'aging': 2, 'critical': 3, 'parked': 0, 'done': 0}
    assert rows['critical']['age_days'] == 21
    attention = {r['client_name'] for r in api.get(JOBS, {'attention': 1}).data['results']}
    assert attention == {'watch', 'aging', 'critical'}

    # The thresholds are a setting (the sheet's Settings tab).
    assert api.patch(f'{JOBS}settings/', {'watch_days': 3, 'aging_days': 5, 'critical_days': 30},
                     format='json').status_code == 200
    rows = {r['client_name']: r for r in api.get(JOBS).data['results']}
    assert (rows['watch']['severity'], rows['critical']['severity']) == (2, 2)
    bad = api.patch(f'{JOBS}settings/', {'watch_days': 10}, format='json')
    assert bad.status_code == 400 and JobSettings.load().watch_days == 3


# ── client / site links ──────────────────────────────────────────────────────

def test_client_and_site_links(api):
    acme = make_client('acme', company_name='Acme')
    other = make_client('other', company_name='Other')
    office = Site.objects.create(client=acme, name='Head office')
    # Picking a site links its client.
    job = create(api, site=office.pk)
    assert job['client'] == {'id': acme.pk, 'name': 'Acme'} and job['site']['name'] == 'Head office'
    url = f"{JOBS}{job['id']}/"
    res = api.patch(url, {'client': other.pk}, format='json')
    assert res.status_code == 400 and 'site' in res.data
    # Removing the client link removes its site too.
    cleared = api.patch(url, {'client': None}, format='json').data
    assert cleared['client'] is None and cleared['site'] is None
    # Only client accounts can be linked; only staff can be assigned.
    staffer = make_staff('sediek')
    assert api.patch(url, {'client': staffer.pk}, format='json').status_code == 400
    assert api.patch(url, {'assigned_to': [acme.pk]}, format='json').status_code == 400


# ── comments ─────────────────────────────────────────────────────────────────

def test_comments_thread_and_only_the_author_edits(api, boss):
    job = create(api, comment='From the phone call')
    url = f"{JOBS}{job['id']}/comments/"
    assert [c['body'] for c in job['comments']] == ['From the phone call']
    other = make_staff('thandi', perms=ALL_JOB_PERMS)
    mine = api_for(other).post(url, {'body': 'Parts ordered'}, format='json').data
    detail = api.get(f"{JOBS}{job['id']}/").data
    assert [(c['author']['name'], c['mine']) for c in detail['comments']] == [('boss', True), ('thandi', False)]
    assert detail['comment_count'] == 2

    assert api.patch(f"{url}{mine['id']}/", {'body': 'Hacked'}, format='json').status_code == 403
    edited = api_for(other).patch(f"{url}{mine['id']}/", {'body': 'Parts ordered (ETA Fri)'}, format='json').data
    assert edited['edited_at'] and JobComment.objects.get(pk=mine['id']).body == 'Parts ordered (ETA Fri)'
    assert api.get(JOBS, {'q': 'ETA Fri'}).data['count'] == 1


# ── permissions ──────────────────────────────────────────────────────────────

def test_each_action_needs_its_permission(boss):
    job = create(api_for(boss))
    url = f"{JOBS}{job['id']}/"
    reader = api_for(make_staff('reader', perms=['jobs.view']))
    assert reader.get(JOBS).status_code == 200 and reader.get(url).status_code == 200
    assert reader.post(JOBS, {'client_name': 'X'}, format='json').status_code == 403
    assert reader.patch(url, {'task': 'X'}, format='json').status_code == 403
    assert reader.post(f'{url}comments/', {'body': 'X'}, format='json').status_code == 403
    assert reader.delete(url).status_code == 403
    assert reader.patch(f'{JOBS}settings/', {'watch_days': 2}, format='json').status_code == 403

    editor_user = make_staff('editor', perms=['jobs.view', 'jobs.create', 'jobs.edit'])
    editor = api_for(editor_user)
    assert editor.patch(url, {'task': 'Y'}, format='json').status_code == 200
    # Assigning people is its own permission, on create and edit.
    assert editor.patch(url, {'assigned_to': [editor_user.pk]}, format='json').status_code == 403
    assert editor.post(JOBS, {'client_name': 'Z', 'assigned_to': [editor_user.pk]}, format='json').status_code == 403
    assert editor.post(JOBS, {'client_name': 'Z', 'category': cat('Other')}, format='json').status_code == 201

    assigner = api_for(make_staff('assigner', perms=['jobs.view', 'jobs.assign']))
    assert assigner.patch(url, {'assigned_to': [editor_user.pk]}, format='json').status_code == 200
    assert assigner.patch(url, {'assigned_to': [], 'task': 'Q'}, format='json').status_code == 403

    for outsider in (api_for(make_staff('nobody', perms=[])), api_for(make_client('acme')), api_for()):
        assert outsider.get(JOBS).status_code in (401, 403)


# ── trash + audit history ────────────────────────────────────────────────────

def test_trash_restore_and_audit_history(api, boss):
    helper = make_staff('sediek', first_name='Sediek')
    job = create(api, assigned_to=[helper.pk])
    url = f"{JOBS}{job['id']}/"
    api.patch(url, {'status': 'quote_sent', 'assigned_to': [helper.pk, boss.pk]}, format='json')
    api.post(f'{url}comments/', {'body': 'Quote emailed'}, format='json')
    assert api.delete(url).status_code == 204
    assert api.get(url).status_code == 404 and api.get(JOBS).data['count'] == 0

    trash = api.get('/records/trash/').data['results']
    assert [(t['type'], t['name']) for t in trash] == [('jobs.job', 'APS-001 · Acme')]
    preview = api.get(f"/records/trash/jobs.job/{job['id']}/").data['detail']
    assert preview['kind'] == 'job' and preview['comments'][0]['body'] == 'Quote emailed'
    assert api.post(f"/records/trash/jobs.job/{job['id']}/restore/").status_code == 200
    assert api.get(url).status_code == 200

    history = AuditLog.objects.filter(object_type='jobs.job', object_id=str(job['id'])).order_by('id')
    assert [h.action for h in history] == ['created', 'updated', 'commented', 'deleted', 'restored']
    assert history[0].summary == 'Assigned to Sediek'
    assert history[1].changes['status'] == {'label': 'Status', 'old': 'Open', 'new': 'Quote sent'}
    assert history[1].changes['assigned_to'] == {'label': 'Assigned to', 'old': 'Sediek', 'new': 'Sediek; boss'}
    assert all(h.section == 'jobs' for h in history)
    # Staff with jobs.view see a job's own history.
    reader = api_for(make_staff('reader', perms=['jobs.view']))
    assert reader.get(f"/records/history/jobs.job/{job['id']}/").status_code == 200


def test_imported_jobs_without_date_added_sort_by_last_updated(api):
    """The sheet's rule: no Date Added -> after the dated jobs of the same status, newest update first."""
    dated = create(api, client_name='Dated')
    older, newer = create(api, client_name='Older'), create(api, client_name='Newer')
    Job.objects.filter(pk__in=[older['id'], newer['id']]).update(date_added=None)
    age(older['id'], 30)
    age(newer['id'], 3)
    assert [r['client_name'] for r in api.get(JOBS).data['results']] == ['Dated', 'Newer', 'Older']
    assert dated['date_added'] is not None


def test_categories_are_an_editable_list(api, boss):
    names = [c['name'] for c in api.get(f'{JOBS}options/').data['categories']]
    assert names[:3] == ['Call out', 'Open jobs', 'Electrical'] and len(names) == 12

    res = api.post(f'{JOBS}categories/', {'name': '  Solar   install '}, format='json')
    assert res.status_code == 201 and res.data['name'] == 'Solar install' and res.data['order'] == 13
    dup = api.post(f'{JOBS}categories/', {'name': 'call OUT'}, format='json')
    assert dup.status_code == 400 and dup.data['name'] == ['There is already a category with this name.']

    job = create(api, category=res.data['id'])
    url = f"{JOBS}categories/{res.data['id']}/"
    assert api.patch(url, {'name': 'Solar installation'}, format='json').status_code == 200
    assert api.get(f"{JOBS}{job['id']}/").data['category_label'] == 'Solar installation'

    # Switched off: hidden from new jobs, but the job that uses it keeps it.
    assert api.patch(url, {'is_active': False}, format='json').status_code == 200
    bad = api.post(JOBS, {'client_name': 'X', 'category': res.data['id']}, format='json')
    assert bad.status_code == 400 and 'category' in bad.data
    assert api.patch(f"{JOBS}{job['id']}/", {'task': 'Still fine', 'category': res.data['id']},
                     format='json').status_code == 200
    assert api.delete(url).status_code in (403, 405)
    assert AuditLog.objects.filter(object_type='jobs.jobcategory').count() == 3

    editor = api_for(make_staff('editor', perms=['jobs.view', 'jobs.edit']))
    assert editor.post(f'{JOBS}categories/', {'name': 'Nope'}, format='json').status_code == 403


# ── demo data ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('debug', [False, True])
def test_seed_jobs_demo_adds_and_clears(api, settings, debug):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    settings.DEBUG = debug
    real = create(api, client_name='Real job')  # never touched by the demo
    call_command('seed_jobs_demo')
    rows = api.get(JOBS, {'page': 1}).data
    assert rows['count'] == 30
    severities = {r['severity'] for r in rows['results']}
    assert severities == {0, 1, 2, 3}  # every colour shows
    assert {r['status'] for r in rows['results']} == set(Job.Status.values)
    assert User.objects.filter(username__startswith='demo.').count() == 7
    assert not User.objects.get(username='demo.lerenzo').has_usable_password()
    history = AuditLog.objects.filter(object_type='jobs.job').exclude(object_id=str(real['id'])).count()
    assert (history > 0) == debug  # only local runs write history
    with pytest.raises(CommandError):
        call_command('seed_jobs_demo')

    call_command('seed_jobs_demo', '--clear')
    assert list(Job.objects.values_list('client_name', flat=True)) == ['Real job']
    assert not User.objects.filter(username__startswith='demo.').exists()
    assert not AuditLog.objects.filter(object_type='jobs.job').exclude(object_id=str(real['id'])).exists()


def test_sort_unpaid_finished_jobs_first(api):
    """Done / invoiced but not Paid (unpaid, deposit or not set) first, longest waiting first."""
    open_job = create(api, client_name='Open')
    paid = create(api, client_name='Paid', status='invoiced', payment='paid')
    deposit = create(api, client_name='Deposit', status='done', payment='deposit')
    not_set = create(api, client_name='Not set', status='invoiced')
    unpaid = create(api, client_name='Unpaid', status='done', payment='unpaid')
    today = timezone.localdate()
    for job, days in ((deposit, 3), (not_set, 20), (unpaid, 9)):
        Job.objects.filter(pk=job['id']).update(completion_date=today - timedelta(days=days))
    names = [r['client_name'] for r in api.get(JOBS, {'ordering': 'unpaid'}).data['results']]
    assert names == ['Not set', 'Unpaid', 'Deposit', 'Open', 'Paid']
    assert open_job and paid
