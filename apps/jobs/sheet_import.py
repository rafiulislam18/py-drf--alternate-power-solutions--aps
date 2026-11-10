"""
Bring the Jobs tab of the APS Operations sheet (a downloaded .xlsx) into the
dashboard. Run it with ``manage.py import_jobs_sheet <file.xlsx>`` (see there).

How the sheet's values become a job:

- **Job ID** ``APS-123`` keeps its number. A number used twice (APS-125) or a
  row without one gets the next free number, with a note. Divider rows
  ("CALL  OUTS", "Mahdee only") — no client and no task — are skipped.
- **Category** matches the category list ignoring case ("OPEN JOBS" → Open jobs);
  a new one is added ("SOLAR PANEL CLEANING" → Solar panel cleaning).
- **Status** "QUOTE SENT " → Quote sent, …; blank stays blank (no status);
  anything else (e.g. "DISCUSS (MEETING)") becomes Open with a note.
- **Payment** Paid / Paid Deposit / Unpaid / No Charge.
- **Assigned To** "Lucky/Mahdee" → both. Spelling variants are one person
  (``ALIASES``). Each person is an existing staff account (matched on first name
  or username), or a new one: no password, no permissions, the email from the
  sheet's staff directory (Settings tab) when it has one.
- **Dates** real dates, or text like "17-Sept-2026"; text that isn't a date
  ("First week in October", "Done") goes into the note instead.
- **Invoiced** stays text (invoice no. or note); a date becomes "dd/mm/yyyy".
- **Comments** become the job's first comment, from "Operations sheet", together
  with any notes above.
- **Last Updated / Date Added / Completion / Last Alert / Stale Alert** are kept,
  so ages, colours and (later) the alerts carry on where the sheet left off.

Each job gets one "Created" history entry from the system. Jobs with a number
that's already imported are skipped, so running it again only adds new rows.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from apps.accounts.login import identifier_taken
from apps.core.models import ClientProfile
from apps.records.audit import account
from apps.records.models import AuditLog
from apps.staff.models import StaffAccess

from .models import Job, JobCategory, JobComment, format_job_id

SHEET_AUTHOR = 'Operations sheet'

# Spelling variants in Assigned To → one person (09/11/2026, confirmed by the user).
ALIASES = {
    'lorenzo': 'Lerenzo', 'lorenze': 'Lerenzo', 'lerenzo': 'Lerenzo',
    'gerswin': 'Gershwin', 'gershwin': 'Gershwin', 'gurshwain': 'Gershwin',
    'emanual': 'Emmanuel', 'emanuel': 'Emmanuel', 'emmanuel': 'Emmanuel',
    'lenard': 'Lenard', 'leonard': 'Lenard',
    'themba': 'Themba', 'temba': 'Themba',
}

STATUS_MAP = {s.label.upper(): s.value for s in Job.Status}
PAYMENT_MAP = {'PAID': Job.Payment.PAID, 'PAID DEPOSIT': Job.Payment.DEPOSIT, 'UNPAID': Job.Payment.UNPAID,
               'NO CHARGE': Job.Payment.NO_CHARGE}

COLUMNS = {  # our key → the sheet's header (matched ignoring case and spaces)
    'id': 'Job ID', 'category': 'Category', 'client': 'Client / Job', 'task': 'Task / Description',
    'call_date': 'Call Date', 'assessment': 'Assessment', 'follow_up': 'Follow-up Date', 'scheduled': 'Scheduled Date',
    'time': 'Time', 'coc': 'COC Issued', 'invoice': 'Invoiced', 'assigned': 'Assigned To', 'status': 'Status',
    'payment': 'Payment Status', 'completion': 'Completion Date', 'last_alert': 'Last Alert',
    'date_added': 'Date Added', 'last_updated': 'Last Updated', 'comments': 'Comments', 'stale_alert': 'Stale Alert',
}
REQUIRED = ('id', 'category', 'client', 'task', 'status', 'assigned')


class SheetError(Exception):
    """The file isn't something we can import (no Jobs tab, missing columns, …)."""


@dataclass
class Report:
    rows: int = 0
    created: int = 0
    already_imported: list = field(default_factory=list)
    skipped: list = field(default_factory=list)          # (row, why)
    renumbered: list = field(default_factory=list)       # (row, old id, new id)
    notes: list = field(default_factory=list)            # (job id, note)
    categories_added: list = field(default_factory=list)
    people_matched: dict = field(default_factory=dict)   # name → username
    people_created: dict = field(default_factory=dict)   # name → email or ''
    email_skipped: list = field(default_factory=list)    # (name, email, why)
    statuses: dict = field(default_factory=dict)
    first: str = ''
    last: str = ''


def _clean(value):
    return ' '.join(str(value).split()) if value not in (None, '') else ''


def _key(header):
    return re.sub(r'\s+', ' ', str(header or '')).strip().lower()


def _aware(value):
    if isinstance(value, datetime):
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    if isinstance(value, date):
        return timezone.make_aware(datetime.combine(value, time(9)))
    return None


_MONTH_FIX = re.compile(r'\bsept\b', re.I)


def parse_date(value):
    """A date from a sheet cell, or None. Text like "17-Sept-2026" / "21-08-2026" works too."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _MONTH_FIX.sub('Sep', re.sub(r'-+', '-', _clean(value)))
    for fmt in ('%d-%b-%Y', '%d-%m-%Y', '%d %b %Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%B-%Y', '%d %B %Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def split_names(raw):
    return [n.strip() for n in re.split(r'[&/,+]+|\s+and\s+', _clean(raw), flags=re.I) if n.strip()]


def canonical(name):
    return ALIASES.get(name.lower(), name[:1].upper() + name[1:])


def read_workbook(path):
    """(job rows as {key: value, '_row': n}, staff directory {name lower: email})."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except FileNotFoundError:
        raise SheetError(f'File not found: {path}')
    except Exception as exc:  # noqa: BLE001
        raise SheetError(f'Could not open {path} as an .xlsx file: {exc}')
    tabs = {ws.title.strip().lower(): ws for ws in wb.worksheets}
    if 'jobs' not in tabs:
        raise SheetError(f'No "Jobs" tab in the file (tabs: {", ".join(ws.title for ws in wb.worksheets)}).')
    rows = list(tabs['jobs'].iter_rows(values_only=True))
    if not rows:
        raise SheetError('The Jobs tab is empty.')
    header = {_key(h): i for i, h in enumerate(rows[0]) if h not in (None, '')}
    cols = {k: header.get(_key(h)) for k, h in COLUMNS.items()}
    missing = [COLUMNS[k] for k in REQUIRED if cols[k] is None]
    if missing:
        raise SheetError(f'The Jobs tab is missing these columns: {", ".join(missing)}')
    jobs = []
    for n, row in enumerate(rows[1:], start=2):
        if not any(v not in (None, '') and str(v).strip() for v in row):
            continue
        jobs.append({'_row': n, **{k: (row[i] if i is not None and i < len(row) else None) for k, i in cols.items()}})

    directory = {}
    if 'settings' in tabs:
        seen_header = False
        for row in tabs['settings'].iter_rows(values_only=True):
            first, second = (list(row) + [None, None])[:2]
            if _clean(first).lower() == 'name' and _clean(second).lower() == 'email':
                seen_header = True
                continue
            if seen_header and _clean(first):
                directory[canonical(_clean(first)).lower()] = _clean(second)
    wb.close()
    return jobs, directory


class _People:
    """Finds or creates the staff account for each name in Assigned To."""

    def __init__(self, directory, report):
        from apps.staff.views import staff_users
        self.staff = list(staff_users())
        self.directory = directory
        self.report = report
        self.cache = {}
        self.audit = []

    def get(self, raw_name):
        name = canonical(raw_name)
        key = name.lower()
        if key in self.cache:
            return self.cache[key]
        variants = {key} | {alias for alias, target in ALIASES.items() if target.lower() == key}
        found = [u for u in self.staff if u.first_name.strip().lower() in variants or u.username.lower() in variants]
        if found:
            user = sorted(found, key=lambda u: (not u.is_active, u.pk))[0]
            self.report.people_matched[name] = user.username
        else:
            user = self.create(name)
        self.cache[key] = user
        return user

    def create(self, name):
        base = re.sub(r'[^a-z0-9]', '', name.lower()) or 'staff'
        username, n = base, 1
        while identifier_taken(username):
            n += 1
            username = f'{base}{n}'
        email = self.directory.get(name.lower(), '')
        if email and identifier_taken(email):
            self.report.email_skipped.append((name, email, 'already used by another account'))
            email = ''
        user = User(username=username, first_name=name, email=email)
        user.set_unusable_password()  # a superuser sets a password if they should sign in
        user.save()
        ClientProfile.objects.create(user=user, role='admin', verified_email=email)
        StaffAccess.objects.create(user=user, permissions=[])
        self.staff.append(user)
        self.report.people_created[name] = email
        self.audit.append(AuditLog(actor_role='', action=AuditLog.CREATED, object_type='auth.user',
                                   object_id=str(user.pk), object_repr=account(user).object_repr, section='staff',
                                   summary='Staff account added by the jobs sheet import (no password, no permissions)'))
        return user


def _category(raw, report, cache):
    name = _clean(raw) or 'Other'
    key = name.lower()
    if key not in cache:
        found = JobCategory.objects.filter(name__iexact=name).first()
        if found is None:
            pretty = name if name != name.upper() else name[:1] + name[1:].lower()
            last = max([c.order for c in JobCategory.objects.all()], default=0)
            found = JobCategory.objects.create(name=pretty[:60], order=last + 1)
            report.categories_added.append(found.name)
        cache[key] = found
    return cache[key]


@transaction.atomic
def import_jobs(path, dry_run=False):
    """Import the Jobs tab of ``path``. Returns a Report. With ``dry_run`` nothing is kept."""
    rows, directory = read_workbook(path)
    report = Report(rows=len(rows))
    if Job.all_objects.filter(created_by__username__startswith='demo.').exists():
        raise SheetError('Demo jobs are in this database. Remove them first: manage.py seed_jobs_demo --clear')

    def number_of(row):
        m = re.fullmatch(r'APS-?(\d+)', _clean(row['id']).upper())
        return int(m.group(1)) if m else None

    # Numbers the sheet uses, and the ones already taken here.
    imported = set(Job.all_objects.filter(imported_from_sheet=True).values_list('number', flat=True))
    taken_by_dashboard = set(Job.all_objects.filter(imported_from_sheet=False).values_list('number', flat=True))
    sheet_numbers = [number_of(r) for r in rows if number_of(r)]
    clash = sorted(set(sheet_numbers) & taken_by_dashboard)
    if clash:
        raise SheetError('These job numbers already belong to jobs made on the dashboard: '
                         + ', '.join(format_job_id(n) for n in clash[:20])
                         + (' …' if len(clash) > 20 else '') + '. Nothing was imported.')
    next_free = max([*sheet_numbers, *imported, *taken_by_dashboard, 0]) + 1

    people = _People(directory, report)
    categories = {}
    used = set()
    history = []
    now = timezone.now()

    for row in rows:
        r = row['_row']
        client, task = _clean(row['client'])[:200], str(row['task'] or '').strip()
        if not client and not task:
            report.skipped.append((r, f'no client or task ("{_clean(row["id"]) or "blank"}")'))
            continue
        number = number_of(row)
        if number in imported:
            report.already_imported.append(format_job_id(number))
            continue
        notes = []
        if number is None or number in used:
            old = _clean(row['id']) or '(no Job ID)'
            number, next_free = next_free, next_free + 1
            report.renumbered.append((r, old, format_job_id(number)))
            notes.append(f'Was {old} in the sheet' + (' (that number was used twice)' if old.startswith('APS') else ''))
        used.add(number)

        raw_status = _clean(row['status']).upper()
        status = STATUS_MAP.get(raw_status, '' if not raw_status else Job.Status.OPEN)
        if raw_status and raw_status not in STATUS_MAP:
            notes.append(f'Status in the sheet: {_clean(row["status"])}')
        raw_payment = _clean(row['payment']).upper()
        payment = PAYMENT_MAP.get(raw_payment, '')
        if raw_payment and raw_payment not in PAYMENT_MAP:
            notes.append(f'Payment in the sheet: {_clean(row["payment"])}')

        dates = {}
        for key, label in (('call_date', 'Call date'), ('assessment', 'Assessment'), ('follow_up', 'Follow-up date'),
                           ('scheduled', 'Scheduled date'), ('coc', 'COC issued')):
            value = row[key]
            dates[key] = parse_date(value)
            if value not in (None, '') and dates[key] is None and _clean(value):
                notes.append(f'{label} in the sheet: {_clean(value)}')

        invoice = row['invoice']
        invoice = invoice.strftime('%d/%m/%Y') if isinstance(invoice, (date, datetime)) else _clean(invoice)
        when = row['time']
        when = when.strftime('%Hh%M') if isinstance(when, (time, datetime)) else _clean(when)

        added = _aware(row['date_added'])
        completion = parse_date(row['completion'])
        updated = _aware(row['last_updated']) or added or _aware(completion) or now

        job = Job.objects.create(
            number=number, category=_category(row['category'], report, categories), client_name=client, task=task,
            status=status, payment=payment, call_date=dates['call_date'], assessment=dates['assessment'],
            follow_up_date=dates['follow_up'], scheduled_date=dates['scheduled'], scheduled_time=when[:50],
            coc_issued=dates['coc'], invoice=invoice[:100], completion_date=completion, date_added=added,
            last_updated=updated, unpaid_alert_at=_aware(row['last_alert']), stale_alert_at=_aware(row['stale_alert']),
            imported_from_sheet=True,
        )
        job.assigned_to.set({people.get(n).pk for n in split_names(row['assigned'])})
        body = '\n'.join(filter(None, [str(row['comments'] or '').strip(), *notes]))
        if body:
            JobComment.objects.create(job=job, author=None, author_name=SHEET_AUTHOR, body=body, created_at=updated)
        for note in notes:
            report.notes.append((job.job_id, note))
        history.append(AuditLog(created_at=now, actor_role='', action=AuditLog.CREATED, object_type='jobs.job',
                                object_id=str(job.pk), object_repr=str(job)[:300], section='jobs',
                                summary=f'Imported from the Operations sheet (row {r})'))
        report.created += 1
        report.statuses[job.get_status_display() or 'No status'] = \
            report.statuses.get(job.get_status_display() or 'No status', 0) + 1

    AuditLog.objects.bulk_create(people.audit + history)
    numbers = sorted(Job.all_objects.filter(imported_from_sheet=True).values_list('number', flat=True))
    if numbers:
        report.first, report.last = format_job_id(numbers[0]), format_job_id(numbers[-1])
    if dry_run:
        transaction.set_rollback(True)
    return report


@transaction.atomic
def undo_import():
    """Remove every imported job (and its comments and history). Local use only."""
    from django.db import connection
    ids = [str(pk) for pk in Job.all_objects.filter(imported_from_sheet=True).values_list('pk', flat=True)]
    if not ids:
        return 0
    table = AuditLog._meta.db_table
    with connection.cursor() as cur:
        pg = connection.vendor == 'postgresql'
        if pg:
            cur.execute(f'ALTER TABLE {table} DISABLE TRIGGER records_auditlog_append_only')
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            cur.execute(f"DELETE FROM {table} WHERE object_type = 'jobs.job' AND object_id IN "
                        f"({', '.join(['%s'] * len(chunk))})", chunk)
        if pg:
            cur.execute(f'ALTER TABLE {table} ENABLE TRIGGER records_auditlog_append_only')
    Job.all_objects.filter(imported_from_sheet=True).delete()
    return len(ids)

