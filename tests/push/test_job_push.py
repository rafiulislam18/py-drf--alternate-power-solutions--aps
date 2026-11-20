"""
Tests for the job push notifications (apps.push.jobs) and the 07:00 Visits today
run (apps.jobs.tasks.send_visits_today: email + push). ``webpush`` is mocked;
emails land in ``mail.outbox``.
"""

import importlib
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.jobs.models import Job, JobCategory, JobSettings
from apps.jobs.tasks import send_visits_today
from apps.push.models import NotificationPrefs
from tests.client_portal.conftest import api_for, make_client

from .conftest import aps_device, endpoint, make_staff, payloads

pytestmark = pytest.mark.django_db
JOBS = '/jobs/'
JOB_PERMS = ['jobs.view', 'jobs.create', 'jobs.edit', 'jobs.assign']


@pytest.fixture(autouse=True)
def job_categories(db):
    from django.apps import apps
    importlib.import_module('apps.jobs.migrations.0002_seed_categories').seed(apps, None)


@pytest.fixture
def boss(admin_user):
    return admin_user  # a superuser: assigns, but gets no admin alerts unless switched on


@pytest.fixture
def lucky(db):
    return make_staff('lucky', ['jobs.view'], first_name='Lucky', email='lucky@aps.example')


@pytest.fixture
def themba(db):
    return make_staff('themba', ['jobs.view'], first_name='Themba', email='themba@aps.example')


@pytest.fixture
def no_jobs(db):
    return make_staff('nojobs', ['tickets.view'], first_name='Noah', email='noah@aps.example')


def admin_alerts(user, on=True):
    NotificationPrefs.objects.update_or_create(user=user, defaults={'admin_alerts': on})


def category():
    return JobCategory.objects.get(name='Call out')


def new_job(**kw):
    from apps.jobs.services import save_with_next_number
    assigned = kw.pop('assigned', [])
    job = Job(category=category(), client_name=kw.pop('client_name', 'Acme'), task=kw.pop('task', 'Inverter fault'), **kw)
    save_with_next_number(job)
    job.assigned_to.set(assigned)
    return job


def create_via_api(api, capture, **data):
    with capture(execute=True):
        res = api.post(JOBS, {'category': category().pk, 'client_name': 'Acme', 'task': 'Inverter fault', **data},
                       format='json')
    assert res.status_code == 201, res.data
    return res.data


# ── Job assigned ────────────────────────────────────────────────────────────


def test_new_job_pushes_each_assignee(sent, boss, lucky, themba, django_capture_on_commit_callbacks):
    aps_device(lucky, 1)
    aps_device(themba, 2)
    aps_device(boss, 3)
    job = create_via_api(api_for(boss), django_capture_on_commit_callbacks, assigned_to=[lucky.pk, themba.pk],
                         task='Replace the  inverter\nfuse', scheduled_date=str(timezone.localdate()),
                         scheduled_time='10h00')
    assert sorted(u for u, _ in payloads(sent)) == [endpoint(1), endpoint(2)]
    _, payload = payloads(sent)[0]
    assert payload == {
        'app': 'aps',
        'title': f'Job assigned: {job["job_id"]} · Acme',
        'body': f'Visit {timezone.localdate():%a %d %b}, 10h00. Replace the inverter fuse',
        'tag': f'aps-job-{job["id"]}',
        'url': f'/dashboard/jobs/{job["id"]}',
    }


def test_only_newly_added_people_and_never_the_assigner(sent, boss, lucky, themba,
                                                        django_capture_on_commit_callbacks):
    assigner = make_staff('assigner', JOB_PERMS)
    for n, u in enumerate([lucky, themba, assigner]):
        aps_device(u, n)
    api = api_for(assigner)
    job = create_via_api(api, django_capture_on_commit_callbacks, assigned_to=[lucky.pk, assigner.pk])
    assert [u for u, _ in payloads(sent)] == [endpoint(0)]  # not the assigner, though assigned
    sent.reset_mock()
    with django_capture_on_commit_callbacks(execute=True):
        api.patch(f'{JOBS}{job["id"]}/', {'assigned_to': [lucky.pk, themba.pk, assigner.pk]}, format='json')
    assert [u for u, _ in payloads(sent)] == [endpoint(1)]  # Themba only: Lucky was already on it


def test_needs_jobs_view_and_their_switch(sent, boss, lucky, no_jobs, django_capture_on_commit_callbacks):
    aps_device(lucky, 1)
    aps_device(no_jobs, 2)
    NotificationPrefs.objects.create(user=lucky, off_types=['job_assigned'])
    create_via_api(api_for(boss), django_capture_on_commit_callbacks, assigned_to=[lucky.pk, no_jobs.pk])
    sent.assert_not_called()


def test_taken_off_or_trashed_before_the_push(sent, boss, lucky):
    from apps.push.jobs import send_job_assigned_push
    aps_device(lucky, 1)
    job = new_job(assigned=[])
    assert send_job_assigned_push(job.pk, [lucky.pk], boss.pk) is None  # not on the job any more
    job.assigned_to.set([lucky])
    job.trash(boss)
    assert send_job_assigned_push(job.pk, [lucky.pk], boss.pk) is None
    sent.assert_not_called()


def test_client_name_falls_back_to_category(sent, boss, lucky):
    from apps.push.jobs import send_job_assigned_push
    aps_device(lucky, 1)
    job = new_job(client_name='', task='', assigned=[lucky])
    send_job_assigned_push(job.pk, [lucky.pk], boss.pk)
    payload = payloads(sent)[0][1]
    assert payload['title'] == f'Job assigned: {job.job_id} · Call out'
    assert payload['body'] == 'Open the job for the details.'


# ── Visits today: push ──────────────────────────────────────────────────────


@pytest.fixture
def visits(lucky, themba):
    today = timezone.localdate()
    return {
        'a': new_job(client_name='Alpha', scheduled_date=today, scheduled_time='10h00', assigned=[lucky]),
        'b': new_job(client_name='Bravo', scheduled_date=today, scheduled_time='08h00', assigned=[lucky, themba]),
        'c': new_job(client_name='Charlie', scheduled_date=today, scheduled_time='', assigned=[lucky]),
        'd': new_job(client_name='Delta', scheduled_date=today, scheduled_time='13h00', assigned=[lucky]),
        # Not visits today:
        'tomorrow': new_job(client_name='Later', scheduled_date=today + timedelta(days=1), assigned=[lucky]),
        'cancelled': new_job(client_name='Off', scheduled_date=today, status=Job.Status.CANCELLED, assigned=[lucky]),
        'declined': new_job(client_name='No', scheduled_date=today, status=Job.Status.NOT_ACCEPTED, assigned=[lucky]),
    }


def by_endpoint(mock):
    return {url: p for url, p in payloads(mock)}


def test_each_assignee_gets_their_own_visits(sent, settings, visits, lucky, themba, boss):
    aps_device(lucky, 1)
    aps_device(themba, 2)
    aps_device(boss, 3)  # a superuser without "Receive admin alerts": nothing
    send_visits_today.apply()
    got = by_endpoint(sent)
    assert set(got) == {endpoint(1), endpoint(2)}
    b, a, d = visits['b'], visits['a'], visits['d']
    assert got[endpoint(1)] == {
        'app': 'aps',
        'title': 'Visits today: 4',
        'body': f'{b.job_id} Bravo (08h00)\n{a.job_id} Alpha (10h00)\n{d.job_id} Delta (13h00)\n+1 more',
        'tag': f'aps-visits-{timezone.localdate().isoformat()}',
        'url': '/dashboard/jobs?visit=today',
    }
    assert got[endpoint(2)]['title'] == 'Visits today: 1'
    assert got[endpoint(2)]['body'] == f'{b.job_id} Bravo (08h00)'


def test_admin_alerts_get_one_push_with_every_visit(sent, visits, lucky, themba, no_jobs):
    admin_alerts(lucky)       # assigned AND admin alerts: only the full list
    admin_alerts(no_jobs)     # admin alerts but can't see Jobs: nothing
    office = make_staff('office', ['jobs.view'])
    admin_alerts(office)
    for n, u in enumerate([lucky, themba, no_jobs, office]):
        aps_device(u, n)
    send_visits_today.apply()
    got = by_endpoint(sent)
    assert set(got) == {endpoint(0), endpoint(1), endpoint(3)}
    assert got[endpoint(0)]['title'] == got[endpoint(3)]['title'] == 'Visits today: 4'
    assert got[endpoint(1)]['title'] == 'Visits today: 1'
    assert sent.call_count == 3  # Lucky once, not a second time for their own


def test_archived_trashed_and_switched_off(sent, visits, lucky, themba, boss):
    aps_device(lucky, 1)
    aps_device(themba, 2)
    Job.objects.filter(pk=visits['a'].pk).update(archived_at=timezone.now())
    visits['d'].trash(boss)
    NotificationPrefs.objects.create(user=themba, off_types=['visits_today'])
    send_visits_today.apply()
    got = by_endpoint(sent)
    assert set(got) == {endpoint(1)}
    assert got[endpoint(1)]['title'] == 'Visits today: 2'


def test_no_visits_no_push_no_email(sent, lucky):
    aps_device(lucky, 1)
    new_job(scheduled_date=timezone.localdate() - timedelta(days=1), assigned=[lucky])
    assert send_visits_today.apply().get() == 'no visits today'
    sent.assert_not_called()
    assert mail.outbox == []


# ── Visits today: email ─────────────────────────────────────────────────────


@pytest.fixture
def email_on(settings):
    settings.EMAIL_RECIPIENT = 'info@aps.example'
    settings.EMAIL_HOST_USER = 'robot@aps.example'
    settings.FRONTEND_BASE_URL = 'https://aps.example'
    return settings


def test_visits_email_to_the_office_and_each_assignee(email_on, visits, lucky, themba, no_jobs):
    visits['b'].assigned_to.add(no_jobs)
    visits['a'].assigned_to.add(make_staff('noemail', ['jobs.view']))  # no email address: skipped
    send_visits_today.apply()
    to = {m.to[0]: m for m in mail.outbox}
    assert set(to) == {'info@aps.example', 'lucky@aps.example', 'themba@aps.example', 'noah@aps.example'}

    office = to['info@aps.example']
    assert office.subject.startswith('APS: Visits today (') and office.subject.endswith('— 4 booked')
    html = office.body
    for job in (visits['a'], visits['b'], visits['c'], visits['d']):
        assert job.job_id in html and f'https://aps.example/dashboard/jobs/{job.pk}' in html
    assert 'Later' not in html and 'Off' not in html.split('Visits Today')[1].split('</table>')[0]
    assert 'Any time' in html  # Charlie has no time
    assert 'Lucky' in html and 'Themba' in html  # assignees
    assert html.index('Bravo') < html.index('Alpha') < html.index('Delta') < html.index('Charlie')

    mine = to['themba@aps.example'].body
    assert visits['b'].job_id in mine and visits['a'].job_id not in mine
    assert to['themba@aps.example'].subject.endswith('— 1')
    # Can't open Jobs: the list, without links.
    noah = to['noah@aps.example'].body
    assert visits['b'].job_id in noah and '/dashboard/jobs' not in noah


def test_visits_email_switch_pauses_only_the_email(email_on, sent, visits, lucky):
    aps_device(lucky, 1)
    settings_row = JobSettings.load()
    settings_row.visits_email = False
    settings_row.save()
    send_visits_today.apply()
    assert mail.outbox == []
    assert sent.call_count == 1


def test_visits_email_switch_in_job_settings(boss):
    api = api_for(boss)
    assert api.get(f'{JOBS}settings/').data['visits_email'] is True
    res = api.patch(f'{JOBS}settings/', {'visits_email': False}, format='json')
    assert res.status_code == 200 and res.data['visits_email'] is False
    assert JobSettings.load().visits_email is False
    viewer = make_staff('viewer', ['jobs.view'])
    assert api_for(viewer).patch(f'{JOBS}settings/', {'visits_email': True}, format='json').status_code == 403


def test_visits_tile_still_matches(boss, visits):
    res = api_for(boss).get(JOBS)
    assert res.data['summary']['visits_today'] == 4
    assert len(api_for(boss).get(JOBS, {'visit': 'today'}).data['results']) == 4


def test_clients_never_get_staff_types(sent, visits, lucky):
    client = make_client('acme')
    admin_alerts(client)
    aps_device(client, 9)
    send_visits_today.apply()
    assert endpoint(9) not in by_endpoint(sent)
