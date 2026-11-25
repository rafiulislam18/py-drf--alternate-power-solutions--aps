"""
Tool bookings: the dashboard's replacement for the "Tool Bookings" tab of the
APS Operations spreadsheet (Apps Script: 14_Tools.gs).

The sheet mixed a register of tools with a log of who took them. Here:

- ``Tool`` is the register (``TL-001`` …). Identical units without their own
  serial (e.g. three earth leakage testers) are one tool with a quantity;
  a tool with its own serial is its own tool. Only tools "In service" can be
  booked out. Tools go to the trash, never while a unit is out.
- ``Booking`` is one book-out to one technician (``TB-0001`` …), holding one or
  more tools (``BookingItem``). Optional due date, "long-term kit" (no overdue
  warnings), optional link to a job. Bookings are archived automatically once
  every tool is back; one can go to the trash only while nothing is returned.
- ``Movement`` is one book-out or one return (some or all of a booking's
  tools at once). Each waits for a checker ("Waiting for check") until someone
  with "Can check tools" confirms it, unless that person entered it.
- ``Vehicle`` is a company vehicle a booking can name, so the Tools page can
  show what's in each vehicle and what's in the store room.
- ``ToolSettings`` holds the "out a long time" colours, when that counts as
  overdue, the default due date and who gets the alert emails.

Numbers are never reused (items in the trash keep theirs). The rules shared by
the API, the alerts and the import live in ``apps.tools.services``.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.records.models import ArchivableModel, TrashableModel


def format_tool_id(number):
    return f'TL-{number:03d}'


def format_booking_id(number):
    return f'TB-{number:04d}'


def person_name(user):
    return (user.get_full_name() or user.username) if user else ''


class ToolType(models.Model):
    """A group of tools ("Drills", "Testers & meters", …). Switched off = can't
    be picked for new tools, but tools that use it keep it. Never deleted."""

    audit_section = 'tools'

    name = models.CharField(max_length=60, unique=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField('in use', default=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Vehicle(models.Model):
    """A company vehicle ("Lorenzo NP200", CAA 636 441). Switched off = can't
    be picked for new bookings, but bookings that use it keep it. Never deleted."""

    audit_section = 'tools'

    name = models.CharField(max_length=60, unique=True)
    registration = models.CharField(max_length=20, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField('in use', default=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return ' · '.join(x for x in (self.name, self.registration) if x)


class Tool(TrashableModel):
    class Condition(models.TextChoices):
        IN_SERVICE = 'in_service', 'In service'
        IN_REPAIR = 'in_repair', 'In repair'
        LOST = 'lost', 'Lost'
        RETIRED = 'retired', 'Retired'

    number = models.PositiveIntegerField(unique=True, editable=False)
    name = models.CharField(max_length=120)
    type = models.ForeignKey(ToolType, on_delete=models.PROTECT, related_name='tools')
    brand = models.CharField(max_length=120, blank=True)
    model = models.CharField('make / model', max_length=120, blank=True)
    serial = models.CharField('serial no.', max_length=120, blank=True)
    # Identical units booked by count ("Ladder × 4").
    quantity = models.PositiveSmallIntegerField(default=1)
    condition = models.CharField(max_length=12, choices=Condition.choices, default=Condition.IN_SERVICE,
                                 db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    # Brought in from the Operations sheet by ``manage.py import_tools_sheet``.
    imported_from_sheet = models.BooleanField(default=False, editable=False)

    trash_section = 'tools'
    trash_label = 'Tool'
    audit_exclude = {'number', 'created_by', 'created_at', 'imported_from_sheet'}

    class Meta:
        ordering = ['type__order', 'name', 'number']

    @property
    def tool_id(self):
        return format_tool_id(self.number)

    @property
    def label(self):
        """How a booking remembers the tool, even after it's deleted for good."""
        extra = ' '.join(x for x in (self.brand, self.model) if x)
        return ' · '.join(x for x in (self.name, extra, self.serial) if x)[:250]

    def __str__(self):
        return f'{self.tool_id} · {self.name}'

    @classmethod
    def history_extra(cls, pk):
        """A tool's history also shows the bookings it was in."""
        ids = BookingItem.objects.filter(tool_id=pk).values_list('booking_id', flat=True).distinct()
        return [(Booking._meta.label_lower, list(ids))]

    def trash_preview(self, request):
        last = (BookingItem.objects.filter(tool=self).select_related('booking__technician')
                .order_by('-booking__date_out', '-booking__number')[:5])
        return {
            'kind': 'tool',
            'tool_id': self.tool_id,
            'name': self.name,
            'type': self.type.name,
            'brand': self.brand,
            'model': self.model,
            'serial': self.serial,
            'quantity': self.quantity,
            'condition': self.condition,
            'condition_label': self.get_condition_display(),
            'notes': self.notes,
            'bookings': [{'booking_id': i.booking.booking_id, 'technician': person_name(i.booking.technician),
                          'date_out': i.booking.date_out, 'returned': i.return_movement_id is not None}
                         for i in last],
        }


class Booking(TrashableModel, ArchivableModel):
    number = models.PositiveIntegerField(unique=True, editable=False)
    technician = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='tool_bookings')
    date_out = models.DateField(db_index=True)
    due_date = models.DateField('due date', null=True, blank=True)
    # A technician's personal kit: no due date and never overdue.
    long_term_kit = models.BooleanField('long-term kit', default=False)
    job = models.ForeignKey('jobs.Job', on_delete=models.SET_NULL, null=True, blank=True, related_name='tool_bookings')
    # The vehicle the tools travel in (optional; old imported bookings have none).
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, null=True, blank=True, related_name='bookings')
    notes = models.TextField(blank=True)
    # Send the booked-out / returned alerts (the sheet's "Notify" tick box).
    notify = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    # When the daily overdue digest last listed this booking.
    overdue_alert_at = models.DateTimeField(null=True, blank=True, editable=False)
    # When the "overdue" push went out (once per booking); cleared when it stops
    # being overdue, so it can notify again if it goes overdue again (apps.push.tools).
    overdue_push_at = models.DateTimeField(null=True, blank=True, editable=False)
    imported_from_sheet = models.BooleanField(default=False, editable=False)
    # "Mark resolved" (a checker): the problems up to then are dealt with. A problem
    # raised later (a new flag, another damaged return) makes the booking a problem again.
    problem_resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    problem_resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                            blank=True, related_name='+', editable=False)
    problem_resolution = models.CharField('resolution', max_length=200, blank=True, editable=False)

    trash_section = 'tools'
    trash_label = 'Tool booking'
    audit_exclude = {'number', 'created_by', 'created_at', 'overdue_alert_at', 'overdue_push_at', 'imported_from_sheet',
                     'notify', 'technician', 'date_out', 'problem_resolved_at', 'problem_resolved_by', 'problem_resolution'}

    class Meta:
        ordering = ['-date_out', '-number']

    @property
    def booking_id(self):
        return format_booking_id(self.number)

    def __str__(self):
        return f'{self.booking_id} · {person_name(self.technician)}'

    def has_open_items(self):
        return self.items.filter(return_movement__isnull=True).exists()

    def auto_archive_note(self):
        # Every tool back (not waiting for the check): see apps.records.archive.
        if self.items.exists() and not self.has_open_items():
            return 'all tools returned'
        return None

    def auto_unarchive_note(self):
        return 'tools out again'

    def restore_blocked_reason(self):
        from .services import units_out
        open_items = list(self.items.filter(return_movement__isnull=True).select_related('tool'))
        if not open_items:
            return ''
        out = units_out([i.tool_id for i in open_items])
        for item in open_items:
            tool = Tool.all_objects.filter(pk=item.tool_id).first()
            if tool is None or tool.in_trash:
                return f'{item.tool_label} is in the trash. Restore the tool first.'
            if item.quantity > tool.quantity - out.get(tool.pk, 0):
                return f'{tool.name} has been booked out again since this booking was deleted.'
        return ''

    def trash_preview(self, request):
        return {
            'kind': 'tool_booking',
            'booking_id': self.booking_id,
            'technician': person_name(self.technician),
            'date_out': self.date_out,
            'due_date': self.due_date,
            'long_term_kit': self.long_term_kit,
            'job': self.job.job_id if self.job_id else '',
            'vehicle': str(self.vehicle) if self.vehicle_id else '',
            'notes': self.notes,
            'items': [{'label': i.tool_label, 'quantity': i.quantity, 'returned': i.return_movement_id is not None}
                      for i in self.items.all()],
        }


class Movement(models.Model):
    """One book-out or one return: what a checker confirms."""

    class Kind(models.TextChoices):
        OUT = 'out', 'Book-out'
        RETURN = 'return', 'Return'

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='movements')
    kind = models.CharField(max_length=6, choices=Kind.choices)
    date = models.DateField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    recorded_at = models.DateTimeField(default=timezone.now)
    # checked_at set = confirmed. checked_by empty with checked_at set = counted
    # as checked by the sheet import (no checker was written down).
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    checked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    check_note = models.TextField(blank=True)
    problem_flagged = models.BooleanField(default=False)
    # When the booked-out / returned alert went (or the sheet said it went).
    alert_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['date', 'recorded_at', 'id']

    def __str__(self):
        return f'{self.booking.booking_id} {self.get_kind_display().lower()} {self.date:%d %b %Y}'

    @property
    def is_checked(self):
        return self.checked_at is not None


class BookingItem(models.Model):
    """One tool (or some units of it) in a booking. Still out while
    ``return_movement`` is empty. Returning some units of a multi-unit item
    splits it into the returned part and the part still out."""

    class Condition(models.TextChoices):
        OK = 'ok', 'OK'
        DAMAGED = 'damaged', 'Damaged'
        MISSING_PARTS = 'missing_parts', 'Missing parts'

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='items')
    # SET_NULL: a tool deleted for good leaves the booking's record (tool_label) behind.
    tool = models.ForeignKey(Tool, on_delete=models.SET_NULL, null=True, related_name='booking_items')
    tool_label = models.CharField(max_length=250)
    quantity = models.PositiveSmallIntegerField(default=1)
    return_movement = models.ForeignKey(Movement, on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='items')
    return_condition = models.CharField(max_length=14, choices=Condition.choices, blank=True)
    return_note = models.TextField(blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.booking.booking_id}: {self.tool_label}' + (f' ×{self.quantity}' if self.quantity > 1 else '')

    @property
    def is_out(self):
        return self.return_movement_id is None


class ToolSettings(models.Model):
    """One row (pk=1): the "out a long time" colours and the alerts."""

    class Level(models.TextChoices):
        WATCH = 'watch', 'Yellow'
        AGING = 'aging', 'Orange'
        CRITICAL = 'critical', 'Red'

    class DefaultDue(models.TextChoices):
        NONE = 'none', 'No due date'
        TOMORROW = 'tomorrow', 'Next day'
        WEEK = 'week', '1 week'
        TWO_WEEKS = '2weeks', '2 weeks'

    audit_section = 'tools'

    watch_days = models.PositiveSmallIntegerField(default=14)
    aging_days = models.PositiveSmallIntegerField(default=30)
    critical_days = models.PositiveSmallIntegerField(default=60)
    # A booking with no due date counts as overdue from this colour on.
    overdue_from = models.CharField(max_length=8, choices=Level.choices, default=Level.AGING)
    default_due = models.CharField('default due date', max_length=8, choices=DefaultDue.choices,
                                   default=DefaultDue.NONE)
    # The daily 09:00 overdue digest (booked / returned / flagged emails don't depend on it).
    overdue_emails = models.BooleanField('overdue emails', default=True)
    # The office's addresses for every alert, separated by commas.
    alert_emails = models.TextField('alert emails', blank=True)

    class Meta:
        verbose_name = 'tool booking settings'
        verbose_name_plural = 'tool booking settings'

    def __str__(self):
        return 'Tool booking settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={'alert_emails': default_alert_emails()})
        return obj

    def days_for(self, level):
        return {self.Level.WATCH: self.watch_days, self.Level.AGING: self.aging_days,
                self.Level.CRITICAL: self.critical_days}[level]

    @property
    def overdue_days(self):
        return self.days_for(self.overdue_from)

    def alert_list(self):
        return split_emails(self.alert_emails)


def split_emails(text):
    seen, out = set(), []
    for part in str(text or '').replace(';', ',').replace('\n', ',').split(','):
        email = part.strip()
        if email and email.lower() not in seen:
            seen.add(email.lower())
            out.append(email)
    return out


def default_alert_emails():
    """``TOOL_ALERT_EMAILS``, else ``EMAIL_RECIPIENT`` (until someone sets the list on the dashboard)."""
    return ', '.join(split_emails(getattr(settings, 'TOOL_ALERT_EMAILS', '') or getattr(settings, 'EMAIL_RECIPIENT', '')))
