"""manage.py archive_finished: the one-off archive of jobs and tickets finished before the archive existed."""

from io import StringIO

import pytest
from django.core.management import call_command

from apps.client_portal.models import Ticket
from apps.jobs.models import Job, JobCategory
from apps.records.models import AuditLog
from apps.solar_dashboard.models import Site

from ..client_portal.conftest import make_client, make_ticket
from ..jobs.conftest import job_categories  # noqa: F401 (autouse fixture)

pytestmark = pytest.mark.django_db


def job(number, status, payment=''):
    return Job.objects.create(number=number, category=JobCategory.objects.first(), client_name=f'Job {number}',
                              status=status, payment=payment)


@pytest.fixture
def items():
    client = make_client('acme')
    site = Site.objects.create(client=client, name='Head office')
    return {
        'invoiced paid': job(1, 'invoiced', 'paid'),
        'done paid': job(2, 'done', 'paid'),
        'done no charge': job(3, 'done', 'no_charge'),
        'done unpaid': job(4, 'done', 'unpaid'),
        'open paid': job(5, 'open', 'paid'),
        'completed': make_ticket(client, site, status='completed'),
        'cancelled': make_ticket(client, site, status='cancelled'),
        'in progress': make_ticket(client, site, status='in_progress'),
    }


def archived(items):
    return {name for name, obj in items.items() if type(obj).objects.get(pk=obj.pk).is_archived}


def run(*args):
    out = StringIO()
    call_command('archive_finished', *args, stdout=out)
    return out.getvalue()


def test_dry_run_only_counts(items):
    out = run('--dry-run')
    assert 'Jobs: 2 would be archived.' in out and 'Tickets: 2 would be archived.' in out
    assert archived(items) == set()


def test_archives_finished_jobs_and_tickets_once(items):
    out = run()
    assert 'Jobs: archived 2.' in out and 'Tickets: archived 2.' in out
    assert archived(items) == {'invoiced paid', 'done paid', 'completed', 'cancelled'}
    one = Job.objects.get(pk=items['invoiced paid'].pk)
    assert (one.archive_reason, one.archive_note, one.archived_by) == ('auto', 'invoiced and paid', None)
    entry = AuditLog.objects.get(object_type='jobs.job', object_id=str(one.pk))
    assert (entry.action, entry.actor_name, entry.summary) == ('archived', '', 'Archived automatically: invoiced and paid')
    assert 'archived 0' in run()  # nothing left to do


def test_only_jobs_or_only_tickets(items):
    run('--jobs')
    assert archived(items) == {'invoiced paid', 'done paid'}
    run('--tickets')
    assert archived(items) == {'invoiced paid', 'done paid', 'completed', 'cancelled'}


def test_items_someone_unarchived_are_left_alone(items):
    ticket = items['completed']
    ticket.archive(None)
    ticket.unarchive(None)
    out = run()
    assert 'Tickets: archived 1 (1 left alone: unarchived by someone before).' in out
    assert not Ticket.objects.get(pk=ticket.pk).is_archived
