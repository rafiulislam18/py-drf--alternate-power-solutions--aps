import re

from django.contrib.auth.models import User
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.jobs.models import Job
from apps.records.archive import archive_data

from .models import Booking, BookingItem, Movement, Tool, ToolSettings, ToolType, person_name
from .services import STATUS_LABELS, availability, booking_state, free_units, problems_of


def staff_queryset():
    """Active staff accounts: the people tools can be booked out to."""
    from apps.staff.views import staff_users
    return staff_users().filter(is_active=True)


def person(user):
    return {'id': user.pk, 'name': person_name(user)} if user else None


# ── tools ────────────────────────────────────────────────────────────────────

def tool_brief(tool, label=''):
    if tool is None:
        return {'id': None, 'tool_id': '', 'name': label, 'brand': '', 'model': '', 'serial': '', 'quantity': 1}
    return {'id': tool.pk, 'tool_id': tool.tool_id, 'name': tool.name, 'brand': tool.brand, 'model': tool.model,
            'serial': tool.serial, 'quantity': tool.quantity}


def tool_row(tool, out, holders):
    """A tool in the list. ``holders`` = its open items (with booking + technician)."""
    return {
        **tool_brief(tool),
        'type': tool.type_id,
        'type_label': tool.type.name,
        'condition': tool.condition,
        'condition_label': tool.get_condition_display(),
        'notes': tool.notes,
        'units_out': out,
        'free': free_units(tool, out),
        'availability': availability(tool, out),
        'holders': [{
            'technician': person(i.booking.technician),
            'booking': i.booking_id,
            'booking_id': i.booking.booking_id,
            'since': i.booking.date_out,
            'quantity': i.quantity,
            'long_term_kit': i.booking.long_term_kit,
        } for i in sorted(holders, key=lambda i: (i.booking.date_out, i.booking.number))],
        'imported_from_sheet': tool.imported_from_sheet,
    }


class ToolTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolType
        fields = ['id', 'name', 'order', 'is_active']
        extra_kwargs = {'order': {'required': False}, 'name': {'validators': []}}

    def validate_name(self, value):
        name = ' '.join(value.split())
        if not name:
            raise serializers.ValidationError('Enter a name.')
        clash = ToolType.objects.filter(name__iexact=name)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError('There is already a tool type with this name.')
        return name


class ToolWriteSerializer(serializers.ModelSerializer):
    type = serializers.PrimaryKeyRelatedField(
        queryset=ToolType.objects.all(),
        error_messages={'does_not_exist': 'That tool type no longer exists.', 'required': 'Choose a type.',
                        'null': 'Choose a type.'},
    )
    quantity = serializers.IntegerField(min_value=1, max_value=999, required=False)

    class Meta:
        model = Tool
        fields = ['name', 'type', 'brand', 'model', 'serial', 'quantity', 'condition', 'notes']

    def validate_name(self, value):
        name = ' '.join(value.split())
        if not name:
            raise serializers.ValidationError('Enter the tool’s name.')
        return name

    def validate_brand(self, value):
        return ' '.join(value.split())

    def validate_model(self, value):
        return ' '.join(value.split())

    def validate_serial(self, value):
        return ' '.join(value.split())

    def validate_type(self, value):
        if not value.is_active and getattr(self.instance, 'type_id', None) != value.pk:
            raise serializers.ValidationError('That tool type is switched off. Pick another one.')
        return value

    def validate_quantity(self, value):
        out = self.context.get('units_out', 0)
        if self.instance is not None and value < out:
            raise serializers.ValidationError(f'{out} are out right now, so the quantity can’t go below {out}.')
        return value


# ── bookings ─────────────────────────────────────────────────────────────────

def item_data(item):
    return {
        'id': item.pk,
        'tool': tool_brief(item.tool, item.tool_label),
        'tool_label': item.tool_label,
        'quantity': item.quantity,
        'out': item.is_out,
        'return_movement': item.return_movement_id,
        'return_condition': item.return_condition,
        'return_condition_label': item.get_return_condition_display() if item.return_condition else '',
        'return_note': item.return_note,
    }


def movement_data(m, items):
    return {
        'id': m.pk,
        'kind': m.kind,
        'kind_label': m.get_kind_display(),
        'date': m.date,
        'recorded_by': person(m.recorded_by),
        'recorded_at': m.recorded_at,
        'checked': m.is_checked,
        'checked_by': person(m.checked_by),
        'checked_at': m.checked_at,
        'check_note': m.check_note,
        'problem_flagged': m.problem_flagged,
        # Items this movement is about: everything for the book-out, the returned ones for a return.
        'items': [i.pk for i in items if m.kind == Movement.Kind.OUT or i.return_movement_id == m.pk],
    }


def check_info(state):
    """The booking's check cell: the first thing still waiting, else the latest flag, else the latest check."""
    waiting = state['waiting']
    if waiting:
        kinds = [m.kind for m in waiting]
        kind = Movement.Kind.OUT if Movement.Kind.OUT in kinds else Movement.Kind.RETURN
        return {'state': 'waiting', 'kind': kind, 'count': len(waiting)}
    flagged = sorted(state['flagged'], key=lambda m: m.checked_at)
    done = sorted((m for m in state['movements'] if m.is_checked), key=lambda m: (m.checked_at, m.pk))
    last = flagged[-1] if flagged else (done[-1] if done else None)
    if last is None:
        return {'state': 'none'}
    return {'state': 'flagged' if last.problem_flagged else 'checked', 'kind': last.kind,
            'by': person(last.checked_by), 'at': last.checked_at, 'note': last.check_note}


def booking_row(booking, settings, today=None):
    state = booking_state(booking, settings, today)
    items = list(booking.items.all())
    return {
        'id': booking.pk,
        'booking_id': booking.booking_id,
        'technician': person(booking.technician),
        'date_out': booking.date_out,
        'due_date': booking.due_date,
        'long_term_kit': booking.long_term_kit,
        'job': {'id': booking.job_id, 'job_id': booking.job.job_id} if booking.job_id else None,
        'notes': booking.notes,
        'items': [item_data(i) for i in items],
        'units': sum(i.quantity for i in items),
        'open_units': sum(i.quantity for i in state['open']),
        'back_units': sum(i.quantity for i in state['back']),
        'status': state['status'],
        'status_label': STATUS_LABELS[state['status']],
        'overdue': state['overdue'],
        'due_passed': state['due_passed'],
        'days_out': state['days_out'],
        'out_for_days': state['out_for_days'],
        'level': state['level'],
        'last_returned': state['last_return'],
        'check': check_info(state),
        'waiting_count': len(state['waiting']),
        'problem': problem_data(booking),
        'imported_from_sheet': booking.imported_from_sheet,
        **archive_data(booking),
    }


def problem_data(booking):
    """The booking's problems: every flag and bad return, which are still open, and its resolution."""
    found, unresolved = problems_of(booking)
    return {
        'open': bool(unresolved),
        'items': [{'at': at, 'kind': kind, 'text': text, 'by': person(by), 'open': (at, kind, text, by) in unresolved}
                  for at, kind, text, by in found],
        'resolved_at': booking.problem_resolved_at,
        'resolved_by': person(booking.problem_resolved_by),
        'resolution': booking.problem_resolution,
    }


def booking_detail(booking, settings):
    items = list(booking.items.all())
    return {
        **booking_row(booking, settings),
        'notify': booking.notify,
        'created_by': person(booking.created_by),
        'created_at': booking.created_at,
        'movements': [movement_data(m, items) for m in booking.movements.all()],
        'can_delete': not any(not i.is_out for i in items),
    }


def parse_job(value):
    """'APS-123', 'aps 123' or '123' → the job; '' → None."""
    text = ' '.join(str(value or '').split())
    if not text:
        return None
    m = re.fullmatch(r'(?:APS[\s-]*)?0*(\d+)', text, flags=re.I)
    if not m:
        raise serializers.ValidationError('Enter a job number like APS-123.')
    job = Job.objects.filter(number=int(m.group(1))).first()
    if job is None:
        raise serializers.ValidationError(f'There’s no job APS-{int(m.group(1)):03d}.')
    return job


class _ItemLine(serializers.Serializer):
    tool = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, max_value=999, default=1)


class BookOutSerializer(serializers.Serializer):
    technician = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(), error_messages={
            'does_not_exist': 'That person’s staff account no longer exists or is switched off.',
            'required': 'Pick who’s taking the tools.', 'null': 'Pick who’s taking the tools.'})
    items = _ItemLine(many=True, allow_empty=False,
                      error_messages={'empty': 'Pick at least one tool.', 'required': 'Pick at least one tool.'})
    date_out = serializers.DateField()
    due_date = serializers.DateField(required=False, allow_null=True)
    long_term_kit = serializers.BooleanField(default=False)
    job = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=30)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000, default='')
    notify = serializers.BooleanField(default=True)
    confirm_handover = serializers.BooleanField(default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['technician'].queryset = staff_queryset()

    def validate_job(self, value):
        return parse_job(value)

    def validate_notes(self, value):
        return value.strip()


class BookingUpdateSerializer(serializers.Serializer):
    due_date = serializers.DateField(required=False, allow_null=True)
    long_term_kit = serializers.BooleanField(required=False)
    job = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=30)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)

    def validate_job(self, value):
        return parse_job(value)

    def validate_notes(self, value):
        return value.strip()

    def validate(self, attrs):
        booking = self.instance
        kit = attrs.get('long_term_kit', booking.long_term_kit)
        if kit and attrs.get('long_term_kit'):
            attrs['due_date'] = None  # a kit has no due date
        elif kit and attrs.get('due_date'):
            raise serializers.ValidationError({'due_date': 'A long-term kit has no due date. Switch it off first.'})
        due = attrs.get('due_date', booking.due_date)
        if due and due < booking.date_out:
            raise serializers.ValidationError(
                {'due_date': f'The due date can’t be before the date out ({booking.date_out:%d %b %Y}).'})
        return attrs


class _ReturnLine(serializers.Serializer):
    item = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, max_value=999, required=False)
    condition = serializers.ChoiceField(choices=BookingItem.Condition.choices, default=BookingItem.Condition.OK)
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000, default='')
    to_repair = serializers.BooleanField(default=False)


class ReturnSerializer(serializers.Serializer):
    items = _ReturnLine(many=True, allow_empty=False,
                        error_messages={'empty': 'Tick at least one tool that came back.'})
    date = serializers.DateField()
    notify = serializers.BooleanField(default=True)


class CheckSerializer(serializers.Serializer):
    bookings = serializers.ListField(child=serializers.IntegerField(), allow_empty=False, max_length=200)
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000, default='')
    problem = serializers.BooleanField(default=False)


# ── settings ─────────────────────────────────────────────────────────────────

class ToolSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolSettings
        fields = ['watch_days', 'aging_days', 'critical_days', 'overdue_from', 'default_due', 'overdue_emails',
                  'alert_emails']
        extra_kwargs = {f: {'min_value': 1, 'max_value': 999} for f in ('watch_days', 'aging_days', 'critical_days')}

    def validate_alert_emails(self, value):
        from .models import split_emails
        emails = split_emails(value)
        bad = []
        for email in emails:
            try:
                validate_email(email)
            except DjangoValidationError:
                bad.append(email)
        if bad:
            raise serializers.ValidationError(f'Not an email address: {", ".join(bad)}')
        return ', '.join(emails)

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name))  # noqa: E731
        if not get('watch_days') < get('aging_days') < get('critical_days'):
            raise serializers.ValidationError(
                {'aging_days': 'Each colour needs more days than the one before (yellow < orange < red).'})
        return attrs
