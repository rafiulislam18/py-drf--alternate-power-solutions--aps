"""
manage.py import_jobs_sheet: the Jobs tab of the Operations sheet (.xlsx) → jobs,
with the sheet's quirks (dividers, a reused number, text dates, name variants).
"""

from datetime import datetime

import openpyxl
import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import ClientProfile
from apps.jobs.models import Job, JobCategory
from apps.records.models import AuditLog

from ..client_portal.conftest import api_for

pytestmark = pytest.mark.django_db

HEADER = ['Job ID', 'Category', 'Client / Job', 'Task / Description', 'Call Date', 'Assessment', 'Follow-up Date',
          'Scheduled Date', 'Status', 'Payment Status', 'Time ', 'COC Issued', 'Invoiced', 'Assigned To',
          'Completion Date', 'Last Alert', 'Date Added', 'Last Updated', 'Comments', 'Stale Alert']
KEYS = ['id', 'cat', 'client', 'task', 'call', 'assess', 'follow', 'sched', 'status', 'pay', 'time', 'coc', 'inv',
        'who', 'done', 'alert', 'added', 'updated', 'comments', 'stale']
D = datetime(2026, 8, 20, 9, 30)


def row(**values):
    return [values.get(k) for k in KEYS]


ROWS = [
    row(id='APS-001', cat='CALL OUT', client='Isuzu ', task='Epping - EMS installed', call=D, status='INVOICED',
        pay='Paid', inv='INV-02414', who='Lerenzo/Emanuel ', done=D, updated=D, comments='Completed'),
    row(id='APS-002', cat='SOLAR PANEL CLEANING', client='Rialto', task='No power', assess='17-Sept-2026',
        sched='First week in October', status='QUOTE SENT ', pay='Paid Deposit', time='10h00 - 10h30',
        who='Lorenzo & Gurshwain', updated=D),
    row(id='APS-002', cat='Open Jobs', client='Second 002', task='Duplicate number', status='DONE', pay='No Charge',
        inv=datetime(2026, 3, 2), who='Gershwin'),
    row(id='CALL  OUTS'),
    row(id='APS-004', cat='CALL OUT', client='No status yet', task='Old call out', who='Brian'),
    row(id='APS-005', cat='OTHER', client='Mr Rawoot', task='LED strips', status='DISCUSS (MEETING)', who='Tenielle'),
]


def make_sheet(path, rows=ROWS):
    wb = openpyxl.Workbook()
    jobs = wb.active
    jobs.title = 'Jobs'
    jobs.append(HEADER)
    for r in rows:
        jobs.append(r)
    staff = wb.create_sheet('Settings')
    for r in [('STAFF DIRECTORY (add name + email below)',), ('Name', 'Email'), ('Tenielle', 'info@aps.example'),
              ('Gershwin', None)]:
        staff.append(r)
    wb.save(path)
    return str(path)


@pytest.fixture
def api_boss(db):
    user = User.objects.create_user('boss', password='x', is_staff=True, is_superuser=True)
    ClientProfile.objects.create(user=user, role='admin')
    return api_for(user)


def test_import_reads_every_quirk_of_the_sheet(tmp_path, capsys):
    path = make_sheet(tmp_path / 'APS Operations.xlsx')
    call_command('import_jobs_sheet', path)
    out = capsys.readouterr().out
    assert 'Jobs imported: 5' in out and 'row 5: no client or task ("CALL OUTS")' in out
    assert 'APS-002 → APS-006' in out

    jobs = {j.number: j for j in Job.objects.select_related('category').prefetch_related('assigned_to', 'comments')}
    assert sorted(jobs) == [1, 2, 4, 5, 6]
    one = jobs[1]
    assert (one.client_name, one.status, one.payment, one.invoice, one.category.name) == \
        ('Isuzu', 'invoiced', 'paid', 'INV-02414', 'Call out')
    assert sorted(u.first_name for u in one.assigned_to.all()) == ['Emmanuel', 'Lerenzo']
    assert one.completion_date == D.date() and one.date_added is None and one.imported_from_sheet
    assert one.comments.get().author_name == 'Operations sheet'

    two = jobs[2]
    assert (two.status, two.payment, two.scheduled_time, str(two.assessment)) == \
        ('quote_sent', 'deposit', '10h00 - 10h30', '2026-09-17')
    assert two.category.name == 'Solar panel cleaning'  # a new category
    assert sorted(u.first_name for u in two.assigned_to.all()) == ['Gershwin', 'Lerenzo']  # variants merged
    assert two.scheduled_date is None and 'First week in October' in two.comments.get().body

    dup = jobs[6]
    assert (dup.payment, dup.invoice) == ('no_charge', '02/03/2026')
    assert 'Was APS-002 in the sheet' in dup.comments.get().body
    assert jobs[4].status == '' and jobs[5].status == 'open'
    assert 'DISCUSS (MEETING)' in jobs[5].comments.get().body

    # People: one account each, no way to sign in, email from the staff directory.
    tenielle = User.objects.get(first_name='Tenielle')
    assert tenielle.email == 'info@aps.example' and not tenielle.has_usable_password()
    assert tenielle.staff_access.permissions == []
    assert User.objects.filter(first_name='Gershwin').count() == 1
    assert AuditLog.objects.filter(object_type='jobs.job', action='created').count() == 5

    # Again (e.g. a newer download): nothing doubles up.
    call_command('import_jobs_sheet', path)
    assert 'Already imported earlier, left as they are: 5' in capsys.readouterr().out
    assert Job.objects.count() == 5


def test_dry_run_keeps_nothing_and_existing_accounts_are_reused(tmp_path, capsys):
    brian = User.objects.create_user('brian.k', first_name='Brian')
    ClientProfile.objects.create(user=brian, role='admin')
    path = make_sheet(tmp_path / 's.xlsx')
    call_command('import_jobs_sheet', path, '--dry-run')
    out = capsys.readouterr().out
    assert 'DRY RUN' in out and 'Jobs that would be imported: 5' in out and 'Brian → brian.k' in out
    assert not Job.objects.exists() and not User.objects.filter(first_name='Lerenzo').exists()
    assert not JobCategory.objects.filter(name='Solar panel cleaning').exists()


def test_import_refuses_clashes_demo_data_and_bad_files(tmp_path, api_boss):
    path = make_sheet(tmp_path / 's.xlsx')
    with pytest.raises(CommandError, match='File not found'):
        call_command('import_jobs_sheet', str(tmp_path / 'nope.xlsx'))
    openpyxl.Workbook().save(tmp_path / 'empty.xlsx')
    with pytest.raises(CommandError, match='No "Jobs" tab'):
        call_command('import_jobs_sheet', str(tmp_path / 'empty.xlsx'))

    res = api_boss.post('/jobs/', {'client_name': 'Made on the dashboard',
                                   'category': JobCategory.objects.get(name='Other').pk}, format='json')
    assert res.data['job_id'] == 'APS-001'
    with pytest.raises(CommandError, match='APS-001'):
        call_command('import_jobs_sheet', path)
    assert Job.objects.count() == 1

    Job.all_objects.all().delete()
    call_command('seed_jobs_demo')
    with pytest.raises(CommandError, match='seed_jobs_demo --clear'):
        call_command('import_jobs_sheet', path)


def test_undo_is_local_only(tmp_path, settings):
    call_command('import_jobs_sheet', make_sheet(tmp_path / 's.xlsx'))
    settings.DEBUG = False
    with pytest.raises(CommandError, match='local testing only'):
        call_command('import_jobs_sheet', '--undo')
    settings.DEBUG = True
    call_command('import_jobs_sheet', '--undo')
    assert not Job.all_objects.exists()
    assert not AuditLog.objects.filter(object_type='jobs.job').exists()
