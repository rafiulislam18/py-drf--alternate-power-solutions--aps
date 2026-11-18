"""
The tool booking rules, shared by the API, the alerts and the import.

- **Availability:** a tool's free units = its quantity minus the units on
  items still out (in bookings that aren't in the trash). Only tools "In
  service" with enough free units can be booked out.
- **Handover:** a single-unit tool that's out with someone else can be booked
  only with ``confirm_handover`` (they handed it over): that records its return
  on the old booking first.
- **Colours:** a booking still out gets a strip by days out (watch / aging /
  critical from ``ToolSettings``); a passed due date is always red. Long-term
  kits and returned bookings get none.
- **Overdue:** not a kit, tools still out, and the due date has passed, or
  there's no due date and it's been out for ``overdue_days`` (the colour chosen
  in the settings).
- **Checks:** every book-out and return waits for a checker, unless the person
  who entered it can check tools: then it's confirmed straight away.
- **Archive:** a return that brings the last tool back archives the booking
  (``apps.records.archive``); its checks can still be waiting.
- **Problems:** a booking has a problem when a check was flagged or a tool came
  back Damaged / Missing parts. A checker marks it resolved with a note; a
  problem raised after that (a new flag, another bad return) counts again.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Exists, Max, OuterRef, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.records import archive, audit
from apps.staff.access import has_perm

from .models import Booking, BookingItem, Movement, Tool, person_name


# ── numbers ──────────────────────────────────────────────────────────────────

def next_number(model):
    """One more than the highest number ever given (items in the trash included)."""
    return (model.all_objects.aggregate(n=Max('number'))['n'] or 0) + 1


def save_with_next_number(obj, attempts=5):
    """Give an unsaved tool or booking the next number and save it; two saves
    racing for one number just take the next."""
    for attempt in range(attempts):
        try:
            with transaction.atomic():
                obj.number = next_number(type(obj))
                obj.save()
                return obj
        except IntegrityError:
            if attempt == attempts - 1:
                raise
    return obj


# ── availability ─────────────────────────────────────────────────────────────

def open_items():
    """Items still out, in bookings that aren't in the trash."""
    return BookingItem.objects.filter(return_movement__isnull=True, booking__deleted_at__isnull=True)


def units_out(tool_ids=None):
    """{tool id: units out right now}."""
    qs = open_items()
    if tool_ids is not None:
        qs = qs.filter(tool_id__in=list(tool_ids))
    return {row['tool_id']: row['n'] for row in qs.values('tool_id').annotate(n=Sum('quantity'))}


def availability(tool, out):
    """'oos' (not in service), 'available', 'partly' or 'out'."""
    if tool.condition != Tool.Condition.IN_SERVICE:
        return 'oos'
    if not out:
        return 'available'
    return 'out' if out >= tool.quantity else 'partly'


def free_units(tool, out):
    return max(tool.quantity - out, 0) if tool.condition == Tool.Condition.IN_SERVICE else 0


# ── colours, overdue and status ──────────────────────────────────────────────

def level_for(days, settings):
    if days >= settings.critical_days:
        return 3
    if days >= settings.aging_days:
        return 2
    if days >= settings.watch_days:
        return 1
    return 0


def booking_state(booking, settings, today=None):
    """Everything a list row needs to show where a booking stands. Uses the
    prefetched ``items`` and ``movements``."""
    today = today or timezone.localdate()
    items = list(booking.items.all())
    movements = list(booking.movements.all())
    open_ = [i for i in items if i.is_out]
    back = [i for i in items if not i.is_out]
    days_out = (today - booking.date_out).days
    due_passed = bool(open_ and booking.due_date and booking.due_date < today)
    if booking.long_term_kit or not open_:
        level = 0
    else:
        level = max(level_for(days_out, settings), 3 if due_passed else 0)
    overdue = bool(open_) and not booking.long_term_kit and (
        due_passed or (booking.due_date is None and days_out >= settings.overdue_days))
    if not open_:
        status = 'returned'
    elif overdue:
        status = 'overdue'
    elif back:
        status = 'partly'
    else:
        status = 'out'
    returns = [m for m in movements if m.kind == Movement.Kind.RETURN]
    last_return = max((m.date for m in returns), default=None)
    return {
        'open': open_,
        'back': back,
        'days_out': days_out,
        # How long it was (or has been) out: to the last return once everything's back.
        'out_for_days': (last_return - booking.date_out).days if not open_ and last_return else days_out,
        'level': level,
        'overdue': overdue,
        'due_passed': due_passed,
        'status': status,
        'last_return': last_return,
        'waiting': [m for m in movements if not m.is_checked],
        'flagged': [m for m in movements if m.problem_flagged],
        'movements': movements,
    }


STATUS_LABELS = {'out': 'Out', 'overdue': 'Overdue', 'partly': 'Partly returned', 'returned': 'Returned'}

HAS_OPEN = Exists(BookingItem.objects.filter(booking=OuterRef('pk'), return_movement__isnull=True))
HAS_BACK = Exists(BookingItem.objects.filter(booking=OuterRef('pk'), return_movement__isnull=False))
WAITING = Exists(Movement.objects.filter(booking=OuterRef('pk'), checked_at__isnull=True))
FLAGGED = Exists(Movement.objects.filter(booking=OuterRef('pk'), problem_flagged=True))
BAD_CONDITIONS = (BookingItem.Condition.DAMAGED, BookingItem.Condition.MISSING_PARTS)


def _problems(after_resolution):
    """Exists: a flagged check or a bad return (only those after the booking's
    resolution when ``after_resolution``; with no resolution that matches none)."""
    flags = Movement.objects.filter(booking=OuterRef('pk'), problem_flagged=True)
    bad = BookingItem.objects.filter(booking=OuterRef('pk'), return_condition__in=BAD_CONDITIONS)
    if after_resolution:
        flags = flags.filter(checked_at__gt=OuterRef('problem_resolved_at'))
        bad = bad.filter(return_movement__recorded_at__gt=OuterRef('problem_resolved_at'))
    return Exists(flags) | Exists(bad)


PROBLEM_ANY = _problems(False)
PROBLEM_AFTER = _problems(True)


def with_flags(qs):
    """Annotate bookings with has_open / has_back / waiting / flagged / problem_any /
    problem_after for filtering."""
    return qs.annotate(has_open=HAS_OPEN, has_back=HAS_BACK, waiting=WAITING, flagged=FLAGGED,
                       problem_any=PROBLEM_ANY, problem_after=PROBLEM_AFTER)


def problem_q():
    """Bookings with a problem nobody has resolved yet (for :func:`with_flags` querysets)."""
    return Q(problem_resolved_at__isnull=True, problem_any=True) | Q(problem_after=True)


def problems_of(booking):
    """[(when, kind, text, who)] for the booking's flags and bad returns, oldest first
    (uses the prefetched movements and items), and whether any is unresolved."""
    found = []
    movements = {m.pk: m for m in booking.movements.all()}
    for m in movements.values():
        if m.problem_flagged and m.checked_at:
            found.append((m.checked_at, 'flag', m.check_note, m.checked_by))
    for item in booking.items.all():
        if item.return_condition in BAD_CONDITIONS and item.return_movement_id in movements:
            m = movements[item.return_movement_id]
            name = item.tool.name if item.tool_id and item.tool else item.tool_label
            text = f'{name}: {item.get_return_condition_display().lower()}'
            found.append((m.recorded_at, 'return', text + (f' ({item.return_note})' if item.return_note else ''),
                          m.recorded_by))
    found.sort(key=lambda p: p[0])
    resolved = booking.problem_resolved_at
    unresolved = [p for p in found if resolved is None or p[0] > resolved]
    return found, unresolved


def overdue_q(settings, today=None):
    """Overdue bookings, for a queryset annotated by :func:`with_flags`."""
    today = today or timezone.localdate()
    return Q(has_open=True, long_term_kit=False) & (
        Q(due_date__lt=today) | Q(due_date__isnull=True, date_out__lte=today - timedelta(days=settings.overdue_days)))


DUE_SOON_DAYS = 7


def due_soon_q(today=None):
    """Still out, not a kit, due today or within the next ``DUE_SOON_DAYS`` days
    (for a queryset annotated by :func:`with_flags`). Past due is overdue instead."""
    today = today or timezone.localdate()
    return Q(has_open=True, long_term_kit=False, due_date__gte=today,
             due_date__lte=today + timedelta(days=DUE_SOON_DAYS))


def status_q(status, settings, today=None):
    overdue = overdue_q(settings, today)
    return {
        'returned': Q(has_open=False),
        'overdue': overdue,
        'partly': Q(has_open=True, has_back=True) & ~overdue,
        'out': Q(has_open=True, has_back=False) & ~overdue,
    }.get(status)


# ── checks ───────────────────────────────────────────────────────────────────

def can_check(user):
    return has_perm(user, 'tools.check')


def _check_if_checker(movement, user):
    """Someone who can check tools confirms what they enter themselves."""
    if can_check(user):
        movement.checked_by = user
        movement.checked_at = timezone.now()


def _items_text(items):
    names = [i.tool.name if i.tool_id and i.tool else i.tool_label for i in items]
    qty = [f' ×{i.quantity}' if i.quantity > 1 else '' for i in items]
    return ', '.join(f'{n}{q}' for n, q in zip(names, qty))


class HandoverNeeded(Exception):
    """Some tools are out with someone else: book them only if they were handed over."""

    def __init__(self, conflicts):
        super().__init__('handover')
        self.conflicts = conflicts


def _invalid(field, message):
    raise ValidationError({field: [message]})


# ── book out ─────────────────────────────────────────────────────────────────

@transaction.atomic
def book_out(user, *, technician, items, date_out, due_date=None, long_term_kit=False, job=None, notes='',
             notify=True, confirm_handover=False):
    """Create a booking. ``items`` = [(tool id, quantity)]. Returns
    (booking, [archive events of bookings a handover emptied])."""
    today = timezone.localdate()
    if date_out > today:
        _invalid('date_out', 'The date out can’t be in the future.')
    if long_term_kit:
        due_date = None
    if due_date and due_date < date_out:
        _invalid('due_date', 'The due date can’t be before the date out.')
    wanted = {}
    for tool_id, quantity in items:
        wanted[tool_id] = wanted.get(tool_id, 0) + quantity
    if not wanted:
        _invalid('items', 'Pick at least one tool.')

    tools = {t.pk: t for t in Tool.objects.select_for_update().filter(pk__in=list(wanted))}
    for tool_id in wanted:
        if tool_id not in tools:
            _invalid('items', 'One of the tools no longer exists. Reload and try again.')
    out = units_out(wanted)
    handovers, conflicts = [], []
    for tool_id, quantity in wanted.items():
        tool = tools[tool_id]
        if tool.condition != Tool.Condition.IN_SERVICE:
            _invalid('items', f'{tool.name} is {tool.get_condition_display().lower()}, so it can’t be booked out.')
        free = tool.quantity - out.get(tool_id, 0)
        if quantity <= free:
            continue
        holders = list(open_items().filter(tool=tool).select_related('booking__technician'))
        if tool.quantity == 1 and quantity == 1 and len(holders) == 1:
            held = holders[0]
            if held.booking.technician_id == technician.pk:
                _invalid('items', f'{person_name(technician)} already has {tool.name} ({held.booking.booking_id}).')
            if not confirm_handover:
                conflicts.append({
                    'tool': tool.pk, 'tool_id': tool.tool_id, 'name': tool.name,
                    'brand': ' '.join(x for x in (tool.brand, tool.model) if x),
                    'holder': person_name(held.booking.technician), 'booking': held.booking_id,
                    'booking_id': held.booking.booking_id, 'since': held.booking.date_out,
                })
                continue
            if date_out < held.booking.date_out:
                _invalid('date_out', f'{tool.name} only went out to {person_name(held.booking.technician)} on '
                                     f'{held.booking.date_out:%d %b %Y}: the handover can’t be before that.')
            handovers.append(held)
        elif free <= 0:
            _invalid('items', f'All {tool.quantity} × {tool.name} are out.' if tool.quantity > 1
                     else f'{tool.name} is already out.')
        else:
            _invalid('items', f'Only {free} of {tool.quantity} × {tool.name} are free.')
    if conflicts:
        raise HandoverNeeded(conflicts)

    booking = Booking(technician=technician, date_out=date_out, due_date=due_date, long_term_kit=long_term_kit,
                      job=job, notes=notes, notify=notify, created_by=user)
    save_with_next_number(booking)
    made = BookingItem.objects.bulk_create([
        BookingItem(booking=booking, tool=tools[tid], tool_label=tools[tid].label, quantity=q)
        for tid, q in wanted.items()
    ])
    movement = Movement(booking=booking, kind=Movement.Kind.OUT, date=date_out, recorded_by=user)
    _check_if_checker(movement, user)
    movement.save()
    for item in made:
        item.tool = tools[item.tool_id]
    summary = f'Booked out to {person_name(technician)}: {_items_text(made)}'
    if due_date:
        summary += f' · due {due_date:%d %b %Y}'
    if long_term_kit:
        summary += ' · long-term kit'
    if movement.is_checked:
        summary += ' · checked straight away'
    audit.log(user, 'booked_out', booking, summary=summary)

    events = []
    for held in handovers:
        _, event = return_items(user, held.booking, [{
            'item': held, 'quantity': held.quantity, 'condition': BookingItem.Condition.OK,
            'note': f'Handed over to {person_name(technician)} in {booking.booking_id}',
        }], date=date_out, notify=False)
        if event:
            events.append({**event, 'booking': held.booking_id, 'booking_id': held.booking.booking_id})
    if notify:
        from .emails import notify_booked
        notify_booked(booking)
    return booking, events


# ── return ───────────────────────────────────────────────────────────────────

@transaction.atomic
def return_items(user, booking, lines, *, date, notify=True):
    """Record a return of some or all of ``booking``'s tools. ``lines`` =
    [{item, quantity, condition, note, to_repair}]. Returns (movement, archive event or None)."""
    if date > timezone.localdate():
        _invalid('date', 'The return date can’t be in the future.')
    if date < booking.date_out:
        _invalid('date', f'The return date can’t be before the date out ({booking.date_out:%d %b %Y}).')
    if not lines:
        _invalid('items', 'Tick at least one tool that came back.')
    seen = set()
    for line in lines:
        item = line['item']
        if item.booking_id != booking.pk or not item.is_out:
            _invalid('items', f'{item.tool_label} isn’t out on this booking any more. Reload and try again.')
        if item.pk in seen:
            _invalid('items', 'A tool is listed twice.')
        seen.add(item.pk)
        quantity = line.get('quantity') or item.quantity
        if not 1 <= quantity <= item.quantity:
            _invalid('items', f'{item.tool_label}: return between 1 and {item.quantity}.')
        line['quantity'] = quantity
        line['condition'] = line.get('condition') or BookingItem.Condition.OK
        line['note'] = (line.get('note') or '').strip()
        if line['condition'] != BookingItem.Condition.OK and not line['note']:
            _invalid('items', f'{item.tool_label}: say what’s damaged or missing.')

    met_before = archive.meets_auto_rule(booking)
    movement = Movement(booking=booking, kind=Movement.Kind.RETURN, date=date, recorded_by=user)
    _check_if_checker(movement, user)
    movement.save()
    returned, problems = [], []
    for line in lines:
        item = line['item']
        if line['quantity'] < item.quantity:  # some units back: split off the returned part
            item.quantity -= line['quantity']
            item.save(update_fields=['quantity'])
            item = BookingItem.objects.create(booking=booking, tool_id=item.tool_id, tool_label=item.tool_label,
                                              quantity=line['quantity'])
        item.return_movement = movement
        item.return_condition = line['condition']
        item.return_note = line['note']
        item.save(update_fields=['return_movement', 'return_condition', 'return_note'])
        returned.append(item)
        if line['condition'] != BookingItem.Condition.OK:
            problems.append(f'{item.tool_label}: {item.get_return_condition_display().lower()} ({line["note"]})')
        tool = Tool.objects.filter(pk=item.tool_id).first()
        # Damaged or missing parts can send the tool to repair (a missing part may make it unusable).
        if (line.get('to_repair') and line['condition'] in BAD_CONDITIONS and tool
                and tool.condition == Tool.Condition.IN_SERVICE):
            before = audit.snapshot(tool)
            tool.condition = Tool.Condition.IN_REPAIR
            tool.save(update_fields=['condition'])
            what = 'damaged' if line['condition'] == BookingItem.Condition.DAMAGED else 'with missing parts'
            audit.log_update(user, tool, before, summary=f'Came back {what} in {booking.booking_id}')

    for item in returned:
        item.tool = Tool.all_objects.filter(pk=item.tool_id).first()
    summary = f'Returned {_items_text(returned)}'
    if problems:
        summary += ' · ' + '; '.join(problems)
    note = next((ln['note'] for ln in lines if ln['condition'] == BookingItem.Condition.OK and ln['note']), '')
    if note:
        summary += f' · {note}'
    if movement.is_checked:
        summary += ' · checked straight away'
    audit.log(user, 'returned', booking, summary=summary)
    if notify:
        from .emails import notify_returned
        notify_returned(movement)
    event = archive.apply_auto_archive(booking, met_before, user)
    return movement, event


# ── problems ──

@transaction.atomic
def resolve_problem(user, booking, note):
    """A checker marks the booking's problems resolved, with a short note."""
    note = ' '.join((note or '').split())
    if not note:
        _invalid('note', 'Say how it was resolved, e.g. "Sent for repair" or "Charger found".')
    _, unresolved = problems_of(booking)
    if not unresolved:
        _invalid('note', 'This booking has no open problem to resolve.')
    booking.problem_resolved_at = timezone.now()
    booking.problem_resolved_by = user
    booking.problem_resolution = note[:200]
    booking.save(update_fields=['problem_resolved_at', 'problem_resolved_by', 'problem_resolution'])
    audit.log(user, 'problem_resolved', booking, summary=f'Problem resolved: {note}')
    return booking


# ── confirm ──────────────────────────────────────────────────────────────────

@transaction.atomic
def confirm(user, bookings, *, note='', problem=False):
    """Confirm every movement waiting for a check on ``bookings`` (a checker).
    ``problem`` flags it (the note says what's wrong). Returns (done ids, skipped ids)."""
    note = (note or '').strip()
    if problem and not note:
        _invalid('note', 'Say what the problem is.')
    done, skipped = [], []
    now = timezone.now()
    for booking in bookings:
        waiting = list(booking.movements.select_for_update().filter(checked_at__isnull=True))
        if not waiting:
            skipped.append(booking.pk)
            continue
        for movement in waiting:
            movement.checked_by = user
            movement.checked_at = now
            movement.check_note = note
            movement.problem_flagged = problem
            movement.save(update_fields=['checked_by', 'checked_at', 'check_note', 'problem_flagged'])
        what = ' and '.join(dict.fromkeys(m.get_kind_display().lower() for m in waiting))
        summary = f'Checked the {what}' + (f': {note}' if note else '')
        audit.log(user, 'problem_flagged' if problem else 'checked', booking, summary=summary)
        if problem:
            from .emails import notify_flagged
            notify_flagged(booking, [m.pk for m in waiting])
        done.append(booking.pk)
    return done, skipped
