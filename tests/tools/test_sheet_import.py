"""
manage.py import_tools_sheet: the Tool Bookings tab of the Operations sheet
(.xlsx) → tools and bookings, with the sheet's quirks (blank-name rows that
book the tool above, "In Office", a tool named only by its brand, numbers
stored as floats, checks, kits, name variants).
"""

from datetime import datetime
from io import StringIO

import openpyxl
import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.records.models import AuditLog
from apps.tools.models import Booking, Movement, Tool

from .conftest import make_staff

pytestmark = pytest.mark.django_db

HEADER = ['Notify', 'Tool', 'Brand', 'Quantity ', 'Make/Model', 'Serial No', 'Booked By', 'Date Out', 'Sediek check',
          'Due Date', 'Date Returned', 'Checked By', 'Status (auto)', 'Booking Alert Sent (auto)',
          'Return Alert Sent (auto)', 'Last Overdue Alert (auto)']
KEYS = ['notify', 'tool', 'brand', 'qty', 'model', 'serial', 'who', 'out', 'sediek', 'due', 'back', 'checker',
        'status', 'b_alert', 'r_alert', 'o_alert']


def d(day, month=8):
    return datetime(2026, month, day)


def row(**values):
    return [values.get(k, False if k == 'notify' else None) for k in KEYS]


ROWS = [
    row(notify=True, tool='Cordless Drill Hand', brand='Metabo', serial='SSW18LTX300BL', who='Brian ', out=d(16),
        b_alert=datetime(2026, 8, 19, 22, 22)),
    row(brand='Zacco Drill set', model='Log 002', who='Gurshwain ', out=d(16)),
    row(tool='Hammer Drill SDS', brand='Makita', model='LPR2230', who='Lucky', out=d(16), sediek=d(31, 7),
        checker='Sediek ', status='Out'),
    row(tool='Grinder 230V Big Grinder', brand='Metabo', who='Lucky', out=d(16), sediek=d(31, 7), checker='Sediek '),
    row(tool='Jigsaw', brand='Metabo', who='Leonard ', out=d(16)),
    row(tool='Wall Chaser', brand='Ingo-co', qty=1.0, model='WCL15008', serial=24316580145.0, who='In Office ',
        out=d(16)),
    row(who='Gurshwain ', out=d(28, 7), back=d(12), checker='Michiel  ', status='Return',
        r_alert=datetime(2026, 8, 20, 11, 51)),
    row(who='Emmanuel ', out=d(13), back=d(13), checker='Michiel  ', status='Return'),
    row(tool='Ladder ', qty=4.0, who='Emmanuel ', out=d(17), status='Out'),
    row(who='Brian ', out=d(22, 10)),
    row(),  # an empty row
    row(tool='Thermal Scanner ', qty=1.0, status='Out'),
    row(who='Brian ', out=d(7, 10), due=d(9, 10), back=d(14, 10), status='Returned'),
    row(tool='Label Machine', qty=1.0, who='Lucky', out=d(5, 10), due=d(3, 10)),
    row(tool='cramper ', qty=1.0, who='Lorenzo ', status='Out'),
    row(who='Lucky', back=d(30, 10), o_alert=datetime(2026, 10, 30, 8, 22)),
    row(tool='Green Blower Li-ion ', qty=1.0, back=d(15, 10), status='Returned'),
]


def make_sheet(path, rows=ROWS):
    wb = openpyxl.Workbook()
    wb.active.title = 'Jobs'
    tab = wb.create_sheet('Tool Bookings')
    tab.append(HEADER)
    for r in rows:
        tab.append(r)
    staff = wb.create_sheet('Settings')
    for r in [('Name', 'Email'), ('Brian', 'brian@aps.example'), ('Lucky', None)]:
        staff.append(r)
    wb.save(path)
    return str(path)


def run(path, *args):
    out = StringIO()
    call_command('import_tools_sheet', path, *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def sheet(tmp_path):
    return make_sheet(tmp_path / 'ops.xlsx')


@pytest.fixture
def people(db):
    """Accounts that already exist (the jobs import made these)."""
    return {name: make_staff(name.lower(), perms=[], first_name=name) for name in ('Lucky', 'Sediek', 'Gershwin')}


def by_tool(name):
    return Booking.objects.filter(items__tool__name=name).distinct().order_by('date_out')


def test_tools_from_named_rows_and_brand_only_rows(sheet, people):
    run(sheet)
    names = list(Tool.objects.order_by('number').values_list('number', 'name', 'type__name', 'quantity'))
    assert names[:6] == [
        (1, 'Cordless Drill Hand', 'Drills', 1), (2, 'Zacco Drill set', 'Drills', 1),
        (3, 'Hammer Drill SDS', 'Drills', 1), (4, 'Grinder 230V Big Grinder', 'Grinders', 1),
        (5, 'Jigsaw', 'Saws', 1), (6, 'Wall Chaser', 'Saws', 1)]
    chaser = Tool.objects.get(name='Wall Chaser')
    assert (chaser.serial, chaser.model) == ('24316580145', 'WCL15008')  # no ".0"
    assert Tool.objects.get(name='Ladder').quantity == 4
    assert Tool.objects.get(name='Zacco Drill set').model == 'Log 002'
    assert all(t.imported_from_sheet for t in Tool.objects.all())


def test_blank_name_rows_book_the_tool_above_and_in_office_is_available(sheet, people):
    run(sheet)
    chaser = by_tool('Wall Chaser')
    assert [(b.technician.first_name, b.date_out.isoformat()) for b in chaser] == [
        ('Gershwin', '2026-07-28'), ('Emmanuel', '2026-08-13')]
    assert not Tool.objects.get(name='Wall Chaser').booking_items.filter(return_movement__isnull=True).exists()
    ladder = by_tool('Ladder')
    assert [b.technician.first_name for b in ladder] == ['Emmanuel', 'Brian']  # across the empty row


def test_same_person_and_date_out_is_one_booking_and_the_kits(sheet, people):
    run(sheet)
    lucky = Booking.objects.get(technician=people['Lucky'], date_out='2026-08-16')
    assert sorted(lucky.items.values_list('tool__name', flat=True)) == ['Grinder 230V Big Grinder', 'Hammer Drill SDS']
    assert lucky.long_term_kit
    leonard = Booking.objects.get(technician__first_name='Lenard', date_out='2026-08-16')
    assert leonard.long_term_kit
    brian = Booking.objects.get(technician__first_name='Brian', date_out='2026-08-16')
    assert not brian.long_term_kit  # only Lucky, Leonard and Emmanuel


def test_checks(sheet, people):
    run(sheet)
    lucky = Booking.objects.get(technician=people['Lucky'], date_out='2026-08-16')
    out = lucky.movements.get(kind='out')
    assert out.checked_by == people['Sediek'] and out.checked_at.date().isoformat() == '2026-07-31'
    # A return with a checker is confirmed by them; one without counts as checked by the sheet.
    back = Booking.objects.get(technician=people['Gershwin'], date_out='2026-07-28').movements.get(kind='return')
    assert back.checked_by.first_name == 'Michiel' and back.date.isoformat() == '2026-08-12'
    assert back.alert_sent_at is not None
    scanner = Booking.objects.get(items__tool__name='Thermal Scanner')
    assert all(m.is_checked for m in scanner.movements.all())
    assert scanner.movements.get(kind='return').checked_by is None
    # Still out, no checker: waits for check (the stocktake).
    brian = Booking.objects.get(technician__first_name='Brian', date_out='2026-08-16')
    assert not brian.movements.get(kind='out').is_checked
    assert brian.movements.get(kind='out').alert_sent_at is not None
    # Brian ×2, Gurshwain, Leonard, Emmanuel's ladder, Lucky's label machine, Lorenzo's cramper.
    assert Movement.objects.filter(checked_at__isnull=True).count() == 7


def test_dates_and_odd_rows(sheet, people):
    out = run(sheet)
    label = Booking.objects.get(items__tool__name='Label Machine', technician=people['Lucky'], date_out='2026-10-05')
    assert label.due_date is None  # due before the date out: dropped and reported
    # The Lucky row under the cramper has no date out: the return date is used.
    back = Booking.objects.get(technician=people['Lucky'], date_out='2026-10-30')
    assert 'used the return date' in back.notes and back.overdue_alert_at is not None
    assert list(back.items.values_list('tool__name', flat=True)) == ['cramper']
    cramper = Booking.objects.get(items__tool__name='cramper', technician__first_name='Lerenzo')
    assert 'used the import date' in cramper.notes
    assert not Booking.objects.filter(items__tool__name='Green Blower Li-ion').exists()
    assert 'Green Blower Li-ion: returned 15 Oct 2026 but no one in Booked By' in out
    assert 'Thermal Scanner: the row says Out but no one is in Booked By' in out
    assert 'is before the date out' in out


def test_nothing_is_archived_or_emailed_and_history_is_written(sheet, people, mailoutbox):
    run(sheet)
    assert not Booking.objects.filter(archived_at__isnull=False).exists()
    assert mailoutbox == []
    assert AuditLog.objects.filter(object_type='tools.tool', action='created').count() == Tool.objects.count()
    entry = AuditLog.objects.filter(object_type='tools.booking', summary__contains='rows').first()
    assert entry.summary.startswith('Imported from the Operations sheet (rows ')
    call_command('archive_finished', '--tool-bookings', stdout=StringIO())
    returned = Booking.objects.filter(archived_at__isnull=False)
    assert returned.count() == 4 and all(b.archive_reason == 'auto' for b in returned)


def test_dry_run_keeps_nothing_and_lists_every_spelling(sheet, people):
    before = User.objects.count()
    out = run(sheet, '--dry-run')
    assert Tool.objects.count() == 0 and Booking.objects.count() == 0 and User.objects.count() == before
    assert 'DRY RUN' in out
    assert '"Gurshwain" → Gershwin → matched gershwin  (rows 3, 8)' in out
    assert '"Leonard" → Lenard → new account lenard (no email)  (row 6)' in out
    assert '"Michiel" → new account michiel (no email)  (rows 8, 9)' in out
    assert '"Sediek" → matched sediek  (rows 4, 5)' in out
    assert '"Brian" → new account brian <brian@aps.example>' in out
    assert 'Long-term kits:' in out and 'Ladder ×4' in out


def test_runs_only_once(sheet, people):
    run(sheet)
    with pytest.raises(CommandError, match='already imported'):
        run(sheet)


def test_a_file_without_the_tab(tmp_path, people):
    wb = openpyxl.Workbook()
    wb.active.title = 'Jobs'
    wb.save(tmp_path / 'x.xlsx')
    with pytest.raises(CommandError, match='No "Tool Bookings" tab'):
        run(str(tmp_path / 'x.xlsx'))


def test_undo_is_local_only(sheet, people, settings):
    run(sheet)
    settings.DEBUG = False
    with pytest.raises(CommandError, match='local testing only'):
        call_command('import_tools_sheet', '--undo', stdout=StringIO())
    settings.DEBUG = True
    call_command('import_tools_sheet', '--undo', stdout=StringIO())
    assert Tool.all_objects.count() == 0 and Booking.all_objects.count() == 0
