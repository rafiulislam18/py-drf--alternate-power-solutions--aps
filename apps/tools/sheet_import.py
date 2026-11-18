"""
Bring the "Tool Bookings" tab of the APS Operations sheet (a downloaded
.xlsx) into the dashboard. Run it with ``manage.py import_tools_sheet <file>``.

The tab is a register and a log in one. How it's read:

- **Tools.** A row with a Tool name is a new tool (``TL-`` numbers in sheet
  order). A row without a name that has a Brand, Make/Model or Serial is a new
  tool too, named after its Brand ("Zacco Drill set"). Quantity blank → 1;
  serials stored as numbers (``25212470231.0``) lose the ``.0``. The tool type
  is guessed from the name (``TYPE_WORDS``), else Other.
- **Bookings.** A row's Booked By is a booking of that row's tool; a row with
  no Tool, Brand, Model or Serial is a booking of the last tool above it
  (empty rows in between are skipped). "In Office" means the tool is at the
  office: no booking. A row with no one in Booked By keeps its tool
  (available) and only its booking is left out, with a note. Rows of one person with the same Date Out become one
  booking with several tools (e.g. Lucky's 10 tools on 16 Aug).
  Multi-unit tools are booked as 1 unit (the sheet has no count), and listed.
- **Long-term kits** (user, 18/11/2026): the 16 Aug bookings of Lucky, Leonard
  and Emmanuel (``KIT_PEOPLE`` / ``KIT_DATE``).
- **Dates.** Due Date → due date. Date Returned → a return (one per date and
  checker). A person with no Date Out: Date Returned is used, else today,
  with a note on the booking.
- **Checks** (user, 18/11/2026): Checked By on a returned row confirms that
  return; Checked By or the "Sediek check" date on a row still out confirms
  the book-out (on that date, else the date out). A return with no checker
  counts as checked "from the sheet"; a book-out still out with no checker
  waits for check (a first stocktake).
- **Status** is worked out again; a Date Returned wins over "Out".
- **Alert stamps** (Booking / Return Alert Sent, Last Overdue Alert) are kept.
- **People**: Booked By and Checked By are staff accounts, matched the same
  way as the jobs import (``apps.jobs.sheet_import``: spelling variants, the
  Settings tab's emails); new ones have no password and no permissions.

Nothing is archived automatically and no email is sent (run
``manage.py archive_finished --tool-bookings`` to archive the returned ones).
Each tool and booking gets one "Created" history entry from the system. It
refuses to run twice (tools would be doubled); ``--undo`` works locally.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

from django.db import transaction
from django.utils import timezone

from apps.jobs.sheet_import import Report as PeopleReport
from apps.jobs.sheet_import import SheetError, _clean, _People, canonical, parse_date, staff_directory
from apps.records.models import AuditLog

from .models import Booking, BookingItem, Movement, Tool, ToolType, format_booking_id, format_tool_id

TAB = 'tool bookings'
SHEET_NOTE = 'Imported from the Operations sheet (no checker recorded)'

# Their 16 Aug bookings are personal kits (canonical names: see apps.jobs.sheet_import.ALIASES).
KIT_PEOPLE = {'lucky', 'lenard', 'emmanuel'}
KIT_DATE = date(2026, 8, 16)

IN_OFFICE = {'in office', 'office', 'in the office'}

# First match wins: "Hammer Drill" is a drill, "Demolition breaker" is Other.
TYPE_WORDS = [
    ('Drills', ('drill',)),
    ('Grinders', ('grinder',)),
    ('Saws', ('saw', 'chaser')),
    ('Testers & meters', ('tester', 'tested', 'scanner', 'leakage', 'multi function', 'multimeter', 'laser')),
    ('Crimpers', ('crimp', 'cramper')),
    ('Ladders & access', ('ladder',)),
    ('Hand tools', ('hammer', 'bits', 'wrench', 'spanner', 'screwdriver')),
]

COLUMNS = {  # our key → the sheet's header (matched ignoring case, spaces and "(auto)")
    'tool': 'Tool', 'brand': 'Brand', 'quantity': 'Quantity', 'model': 'Make/Model', 'serial': 'Serial No',
    'booked_by': 'Booked By', 'date_out': 'Date Out', 'sediek_check': 'Sediek check', 'due': 'Due Date',
    'returned': 'Date Returned', 'checked_by': 'Checked By', 'status': 'Status',
    'booking_alert': 'Booking Alert Sent', 'return_alert': 'Return Alert Sent', 'overdue_alert': 'Last Overdue Alert',
}
REQUIRED = ('tool', 'booked_by', 'date_out')


@dataclass
class Report:
    rows: int = 0
    tools: list = field(default_factory=list)              # (tool id, name, type, qty, guessed type?)
    types_guessed: dict = field(default_factory=dict)      # type → count
    bookings: list = field(default_factory=list)           # (booking id, person, date out, tools, kit, state)
    kits: list = field(default_factory=list)               # booking ids
    waiting: list = field(default_factory=list)            # (booking id, person, what)
    in_office: int = 0
    multi_unit: list = field(default_factory=list)         # (tool, qty, [booking ids])
    notes: list = field(default_factory=list)              # (row, note): mapped, but worth a look
    skipped: list = field(default_factory=list)            # (row, why): bookings only; the tool is kept
    people: list = field(default_factory=list)             # (sheet spelling, canonical, outcome, rows)
    email_skipped: list = field(default_factory=list)


def _key(header):
    text = re.sub(r'\(auto\)', '', str(header or ''), flags=re.I)
    return re.sub(r'\s+', '', text).lower()


def _text(value):
    """A cell as text: numbers stored as floats lose the ``.0`` (serials, models)."""
    if value in (None, ''):
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _clean(value)


def _quantity(value):
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 1
    return max(n, 1)


def _stamp(value):
    if isinstance(value, datetime):
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    if isinstance(value, date):
        return timezone.make_aware(datetime.combine(value, time(9)))
    return None


def guess_type(name):
    low = name.lower()
    for type_name, words in TYPE_WORDS:
        if any(w in low for w in words):
            return type_name
    return 'Other'


def read_tab(path):
    """(rows as {key: value, '_row': n}, staff directory)."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except FileNotFoundError:
        raise SheetError(f'File not found: {path}')
    except Exception as exc:  # noqa: BLE001
        raise SheetError(f'Could not open {path} as an .xlsx file: {exc}')
    tabs = {ws.title.strip().lower(): ws for ws in wb.worksheets}
    if TAB not in tabs:
        raise SheetError(f'No "Tool Bookings" tab in the file (tabs: {", ".join(ws.title for ws in wb.worksheets)}).')
    rows = list(tabs[TAB].iter_rows(values_only=True))
    if not rows:
        raise SheetError('The Tool Bookings tab is empty.')
    header = {_key(h): i for i, h in enumerate(rows[0]) if h not in (None, '')}
    cols = {k: header.get(_key(h)) for k, h in COLUMNS.items()}
    missing = [COLUMNS[k] for k in REQUIRED if cols[k] is None]
    if missing:
        raise SheetError(f'The Tool Bookings tab is missing these columns: {", ".join(missing)}')
    out = []
    for n, row in enumerate(rows[1:], start=2):
        values = {k: (row[i] if i is not None and i < len(row) else None) for k, i in cols.items()}
        # The Notify column is tick boxes (True/False) on every row: ignore it when deciding a row is empty.
        if not any(v not in (None, '') and str(v).strip() for v in values.values()):
            continue
        out.append({'_row': n, **values})
    directory = staff_directory(tabs)
    wb.close()
    return out, directory


@dataclass
class _Entry:
    """One sheet row's booking of one tool."""
    row: int
    tool: Tool
    person: str
    date_out: date
    due: date = None
    returned: date = None
    checker: str = ''
    check_date: date = None
    booking_alert: datetime = None
    return_alert: datetime = None
    overdue_alert: datetime = None
    notes: list = field(default_factory=list)


class _PeopleLog:
    """Finds the staff account for each spelling and remembers where it came from."""

    def __init__(self, directory):
        self.report = PeopleReport()
        self.people = _People(directory, self.report, source='tool bookings sheet')
        self.spellings = {}

    def get(self, raw, row=None):
        spelling = _clean(raw)
        entry = self.spellings.setdefault(spelling, {'rows': [], 'user': None})
        if row is not None:
            entry['rows'].append(row)
        if entry['user'] is None:
            entry['user'] = self.people.get(spelling)
        return entry['user']

    def lines(self):
        created = {name.lower() for name in self.report.people_created}
        out = []
        for spelling, entry in sorted(self.spellings.items(), key=lambda kv: kv[0].lower()):
            name = canonical(spelling)
            user = entry['user']
            if name.lower() in created:
                outcome = f'new account {user.username}' + (f' <{user.email}>' if user.email else ' (no email)')
            else:
                outcome = f'matched {user.username}'
            out.append((spelling, name, outcome, entry['rows']))
        return out


@transaction.atomic
def import_tools(path, dry_run=False, today=None):
    """Import the Tool Bookings tab of ``path``. Returns a Report. With ``dry_run`` nothing is kept."""
    rows, directory = read_tab(path)
    if Tool.all_objects.filter(imported_from_sheet=True).exists():
        raise SheetError('Tools from the sheet are already imported. Running it again would add them twice. '
                         '(Locally: import_tools_sheet --undo first.)')
    today = today or timezone.localdate()
    report = Report(rows=len(rows))
    people = _PeopleLog(directory)
    types = {t.name: t for t in ToolType.objects.all()}
    other = types.get('Other') or ToolType.objects.get_or_create(name='Other', defaults={'order': 99})[0]
    number = max(Tool.all_objects.order_by('-number').values_list('number', flat=True)[:1] or [0])
    history = []
    now = timezone.now()
    entries = []
    current = None

    for row in rows:
        r = row['_row']
        name = _clean(row['tool'])
        brand, model, serial = _clean(row['brand']), _text(row['model']), _text(row['serial'])
        if name or brand or model or serial:
            if not name:
                name = brand or model
                report.notes.append((r, f'No Tool name: named "{name}" after its brand/model'))
            type_name = guess_type(name)
            number += 1
            current = Tool.objects.create(
                number=number, name=name[:120], type=types.get(type_name, other), brand=brand[:120],
                model=model[:120], serial=serial[:120], quantity=_quantity(row['quantity']), imported_from_sheet=True)
            report.tools.append((current.tool_id, current.name, current.type.name, current.quantity))
            report.types_guessed[current.type.name] = report.types_guessed.get(current.type.name, 0) + 1
            history.append(AuditLog(created_at=now, actor_role='', action=AuditLog.CREATED, object_type='tools.tool',
                                    object_id=str(current.pk), object_repr=str(current)[:300], section='tools',
                                    summary=f'Imported from the Operations sheet (row {r})'))

        person = _clean(row['booked_by'])
        status = _clean(row['status']).lower()
        out = parse_date(row['date_out'])
        returned = parse_date(row['returned'])
        if current is None:
            if person or out or returned:
                report.skipped.append((r, 'a booking with no tool above it'))
            continue
        if person.lower() in IN_OFFICE:
            report.in_office += 1
            continue
        if not person:
            # The tool stays (available); only the booking can't be made without a person.
            what = [f'{label} {value:%d %b %Y}' for label, value in
                    (('out', out), ('due', parse_date(row['due'])), ('returned', returned)) if value]
            if _clean(row['checked_by']):
                what.append(f'checked by {_clean(row["checked_by"])}')
            if what:
                report.skipped.append((r, f'{current.name} ({current.tool_id}): {", ".join(what)} but no one in '
                                          f'Booked By'))
            elif status.startswith('out'):
                report.notes.append((r, f'{current.name}: the row says Out but no one is in Booked By, so that row '
                                        f'isn’t a booking (the tool is out only if a row below books it)'))
            continue

        entry = _Entry(row=r, tool=current, person=person, date_out=out, returned=returned,
                       checker=_clean(row['checked_by']), check_date=parse_date(row['sediek_check']),
                       booking_alert=_stamp(row['booking_alert']), return_alert=_stamp(row['return_alert']),
                       overdue_alert=_stamp(row['overdue_alert']))
        if entry.date_out is None:
            entry.date_out = returned or today
            note = (f'Date out wasn’t in the sheet: used the return date' if returned
                    else 'Date out wasn’t in the sheet: used the import date')
            entry.notes.append(note)
            report.notes.append((r, f'{current.name} ({person}): {note.lower()}'))
        due = parse_date(row['due'])
        if due and due < entry.date_out:
            report.notes.append((r, f'{current.name} ({person}): due {due:%d %b %Y} is before the date out — dropped'))
        else:
            entry.due = due
        if entry.returned and entry.returned < entry.date_out:
            report.notes.append((r, f'{current.name} ({person}): returned before the date out — return date '
                                    f'set to the date out'))
            entry.returned = entry.date_out
        if entry.returned and status.startswith('out'):
            report.notes.append((r, f'{current.name} ({person}): Status says Out but there is a Date Returned — '
                                    f'imported as returned'))
        # Every spelling with every row it's on, for the dry run's people list.
        people.get(person, r)
        if entry.checker:
            people.get(entry.checker, r)
        elif entry.check_date:
            people.get('Sediek', r)
        entries.append(entry)

    # One booking per person and date out.
    groups = {}
    for entry in entries:
        user = people.get(entry.person)
        groups.setdefault((user.pk, entry.date_out), {'user': user, 'entries': []})['entries'].append(entry)
    next_booking = max(Booking.all_objects.order_by('-number').values_list('number', flat=True)[:1] or [0])
    multi = {}
    for (_, date_out), group in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[1]['entries'][0].row)):
        user, group_entries = group['user'], group['entries']
        next_booking += 1
        kit = canonical(group_entries[0].person).lower() in KIT_PEOPLE and date_out == KIT_DATE
        dues = sorted(e.due for e in group_entries if e.due)
        notes = list(dict.fromkeys(n for e in group_entries for n in e.notes))
        booking = Booking.objects.create(
            number=next_booking, technician=user, date_out=date_out, due_date=None if kit else (dues[0] if dues else None),
            long_term_kit=kit, notes='\n'.join(notes), notify=False, imported_from_sheet=True,
            overdue_alert_at=max((e.overdue_alert for e in group_entries if e.overdue_alert), default=None))
        if kit:
            report.kits.append(booking.booking_id)

        # The book-out: confirmed by Checked By / "Sediek check" on a row still out.
        out_check = next(((e.checker or 'Sediek', e.check_date or date_out) for e in group_entries
                          if not e.returned and (e.checker or e.check_date)), None)
        out_check = out_check or next((('Sediek', e.check_date) for e in group_entries if e.check_date), None)
        all_back = all(e.returned for e in group_entries)
        out_mv = Movement(booking=booking, kind=Movement.Kind.OUT, date=date_out, recorded_at=now,
                          alert_sent_at=max((e.booking_alert for e in group_entries if e.booking_alert), default=None))
        if out_check:
            out_mv.checked_by = people.get(out_check[0])
            out_mv.checked_at = _stamp(out_check[1])
        elif all_back:
            out_mv.checked_at, out_mv.check_note = _stamp(date_out), SHEET_NOTE
        out_mv.save()
        if not out_mv.is_checked:
            report.waiting.append((booking.booking_id, user.first_name or user.username, 'book-out'))

        # Returns: one per return date and checker.
        returns = {}
        for e in group_entries:
            if e.returned:
                returns.setdefault((e.returned, e.checker.lower()), []).append(e)
        return_mv = {}
        for (when, _), back in sorted(returns.items()):
            mv = Movement(booking=booking, kind=Movement.Kind.RETURN, date=when, recorded_at=now,
                          alert_sent_at=max((e.return_alert for e in back if e.return_alert), default=None))
            if back[0].checker:
                mv.checked_by = people.get(back[0].checker)
                mv.checked_at = _stamp(when)
            else:
                mv.checked_at, mv.check_note = _stamp(when), SHEET_NOTE
            mv.save()
            for e in back:
                return_mv[e.row] = mv

        for e in group_entries:
            mv = return_mv.get(e.row)
            BookingItem.objects.create(booking=booking, tool=e.tool, tool_label=e.tool.label, quantity=1,
                                       return_movement=mv, return_condition=BookingItem.Condition.OK if mv else '')
            if e.tool.quantity > 1:
                multi.setdefault(e.tool.pk, (e.tool, []))[1].append(booking.booking_id)
        state = 'returned' if all_back else ('partly returned' if returns else 'out')
        report.bookings.append((booking.booking_id, user.first_name or user.username, date_out,
                                [e.tool.name for e in group_entries], kit, state))
        history.append(AuditLog(created_at=now, actor_role='', action=AuditLog.CREATED, object_type='tools.booking',
                                object_id=str(booking.pk), object_repr=str(booking)[:300], section='tools',
                                summary='Imported from the Operations sheet (row'
                                        f'{"s" if len(group_entries) > 1 else ""} '
                                        f'{", ".join(str(e.row) for e in group_entries)})'))

    report.multi_unit = [(tool.name, tool.quantity, ids) for tool, ids in multi.values()]
    report.people = people.lines()
    report.email_skipped = people.report.email_skipped
    AuditLog.objects.bulk_create(people.people.audit + history)
    if dry_run:
        transaction.set_rollback(True)
    return report


@transaction.atomic
def undo_import():
    """Remove every imported tool and booking (and their history). Local use only."""
    from django.db import connection
    bookings = [str(pk) for pk in Booking.all_objects.filter(imported_from_sheet=True).values_list('pk', flat=True)]
    tools = [str(pk) for pk in Tool.all_objects.filter(imported_from_sheet=True).values_list('pk', flat=True)]
    table = AuditLog._meta.db_table
    with connection.cursor() as cur:
        pg = connection.vendor == 'postgresql'
        if pg:
            cur.execute(f'ALTER TABLE {table} DISABLE TRIGGER records_auditlog_append_only')
        for object_type, ids in (('tools.booking', bookings), ('tools.tool', tools)):
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                cur.execute(f"DELETE FROM {table} WHERE object_type = %s AND object_id IN "
                            f"({', '.join(['%s'] * len(chunk))})", [object_type, *chunk])
        if pg:
            cur.execute(f'ALTER TABLE {table} ENABLE TRIGGER records_auditlog_append_only')
    imported = Booking.all_objects.filter(imported_from_sheet=True)
    BookingItem.objects.filter(booking__in=imported).delete()
    Movement.objects.filter(booking__in=imported).delete()
    imported.delete()
    Tool.all_objects.filter(imported_from_sheet=True).delete()
    return len(tools), len(bookings)


__all__ = ['import_tools', 'undo_import', 'SheetError', 'format_tool_id', 'format_booking_id']
