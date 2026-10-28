"""
Fill the audit history with demo entries, to show how it looks before any
module writes real ones.

    python manage.py seed_audit_demo             # staff & admin entries over the last 2 weeks
    python manage.py seed_audit_demo --clients   # client entries (the "Clients" audit page)
    python manage.py seed_audit_demo --clear     # remove all the demo entries again

Local only: refuses unless DEBUG=True, because audit history can never be
deleted on a live server. Demo entries use their own item types ("demo.job",
"demo.tool_booking", …) and the people are the staff accounts in this database,
so ``--clear`` removes exactly what this command added and nothing else.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.records.models import AuditLog, display_name

DEMO_PREFIX = 'demo.'


def _change(label, old, new):
    return {'label': label, 'old': old, 'new': new}


# (days ago, hour, person index or None = system, action, type, id, name, changes, summary)
STORY = [
    (13, 8, 0, 'created', 'job', 41, 'APS-041 · Urban Growth · inverter fault', {}, ''),
    (13, 9, 1, 'updated', 'job', 41, 'APS-041 · Urban Growth · inverter fault',
     {'assigned_to': _change('Assigned to', '', 'Sediek'), 'status': _change('Status', 'Open', 'In progress')}, ''),
    (13, 10, 0, 'created', 'job', 42, 'APS-042 · COC · 14 Oak Street', {}, ''),
    (12, 11, 2, 'created', 'tool_booking', 7, 'Thermal camera · booked by Thandi', {}, ''),
    (12, 15, 1, 'updated', 'job', 42, 'APS-042 · COC · 14 Oak Street',
     {'scheduled': _change('Scheduled date', '', '20 Oct 2026')}, ''),
    (11, 9, 0, 'created', 'quote', 118, 'Quote · M. Jacobs · 8 kW solar', {}, ''),
    (11, 10, 0, 'updated', 'quote', 118, 'Quote · M. Jacobs · 8 kW solar',
     {'quote_sent': _change('Quote sent', 'No', 'Yes')}, ''),
    (10, 14, 2, 'updated', 'tool_booking', 7, 'Thermal camera · booked by Thandi',
     {'date_returned': _change('Date returned', '', '17 Oct 2026')}, 'Tool returned'),
    (9, 8, 1, 'updated', 'job', 41, 'APS-041 · Urban Growth · inverter fault',
     {'status': _change('Status', 'In progress', 'Done'), 'completion': _change('Completion date', '', '19 Oct 2026')}, ''),
    (9, 16, 0, 'created', 'job', 43, 'APS-043 · Maintenance · geyser timer', {}, ''),
    (8, 9, 0, 'deleted', 'job', 43, 'APS-043 · Maintenance · geyser timer', {}, ''),
    (8, 9, 0, 'restored', 'job', 43, 'APS-043 · Maintenance · geyser timer', {}, ''),
    (8, 12, 1, 'updated', 'job', 41, 'APS-041 · Urban Growth · inverter fault',
     {'invoiced': _change('Invoiced', 'No', 'Yes'), 'status': _change('Status', 'Done', 'Invoiced')}, ''),
    (7, 10, 2, 'created', 'job', 44, 'APS-044 · Open job · DB board upgrade', {}, 'From WhatsApp'),
    (7, 11, 0, 'updated', 'job', 44, 'APS-044 · Open job · DB board upgrade',
     {'category': _change('Category', 'Open jobs', 'Maintenance'), 'assigned_to': _change('Assigned to', '', 'Thandi')}, ''),
    (6, 9, 1, 'updated', 'subscription', 23, 'Subscription · K. Naidoo · cleaning',
     {'comments': _change('Comments', '', 'Called — happy to renew')}, ''),
    (6, 13, 0, 'created', 'quote', 119, 'Quote · Bayside Storage · container office', {}, ''),
    (5, 8, None, 'updated', 'job', 42, 'APS-042 · COC · 14 Oak Street', {},
     'Reminder sent: no update for 7 days'),
    (5, 10, 1, 'updated', 'job', 42, 'APS-042 · COC · 14 Oak Street',
     {'coc_issued': _change('COC issued', 'No', 'Yes'), 'status': _change('Status', 'In progress', 'Done')}, ''),
    (4, 9, 2, 'created', 'tool_booking', 8, 'Cable locator · booked by Thandi', {}, ''),
    (4, 15, 0, 'deleted', 'quote', 119, 'Quote · Bayside Storage · container office', {}, 'Duplicate request'),
    (3, 8, None, 'updated', 'tool_booking', 8, 'Cable locator · booked by Thandi', {}, 'Overdue reminder sent'),
    (3, 11, 1, 'updated', 'job', 44, 'APS-044 · Open job · DB board upgrade',
     {'status': _change('Status', 'Open', 'Quote sent')}, ''),
    (2, 9, 0, 'created', 'job', 45, 'APS-045 · MLP · battery install', {}, ''),
    (2, 10, 0, 'updated', 'job', 45, 'APS-045 · MLP · battery install',
     {'assigned_to': _change('Assigned to', '', 'Sediek & Thandi'), 'scheduled': _change('Scheduled date', '', '29 Oct 2026')},
     ''),
    (2, 14, 2, 'updated', 'tool_booking', 8, 'Cable locator · booked by Thandi',
     {'date_returned': _change('Date returned', '', '26 Oct 2026')}, 'Tool returned'),
    (1, 3, None, 'purged', 'job', 12, 'APS-012 · Other · old test job', {},
     'Deleted for good automatically (in the trash too long)'),
    (1, 9, 1, 'updated', 'job', 41, 'APS-041 · Urban Growth · inverter fault',
     {'payment': _change('Payment status', 'Unpaid', 'Paid')}, ''),
    (1, 12, 0, 'updated', 'quote', 118, 'Quote · M. Jacobs · 8 kW solar',
     {'processed': _change('Processed', 'No', 'Yes'), 'comments': _change('Comments', '', 'Accepted — job APS-045')}, ''),
    (0, 8, 2, 'created', 'job', 46, 'APS-046 · COC · 3 Vine Road', {}, ''),
    (0, 9, 1, 'updated', 'job', 46, 'APS-046 · COC · 3 Vine Road',
     {'follow_up': _change('Follow-up date', '', '30 Oct 2026')}, ''),
    (0, 10, 0, 'deleted', 'tool_booking', 7, 'Thermal camera · booked by Thandi', {}, ''),
]


# What clients did on their side: (days ago, hour, client index, action, type, id, name, changes, summary)
CLIENT_STORY = [
    (13, 7, 0, 'signed_in', 'account', 0, '', {}, ''),
    (13, 7, 0, 'created', 'ticket', 1201, 'APS-1201 — Inverter showing fault code F23', {}, ''),
    (13, 8, 0, 'message_sent', 'ticket', 1201, 'APS-1201 — Inverter showing fault code F23', {},
     'It started after the power came back on last night. Photos attached.'),
    (12, 19, 1, 'signed_up', 'account', 1, '', {}, ''),
    (12, 19, 1, 'email_confirmed', 'account', 1, '', {}, ''),
    (12, 20, 1, 'created', 'site', 31, 'Main house', {}, ''),
    (11, 9, 1, 'updated', 'site', 31, 'Main house',
     {'address': _change('Address', '', '14 Oak Street, Constantia'), 'has_battery': _change('Has battery', 'No', 'Yes')},
     ''),
    (10, 14, 0, 'message_sent', 'ticket', 1201, 'APS-1201 — Inverter showing fault code F23', {},
     'Thanks, Thursday morning works for us.'),
    (9, 8, 2, 'signed_in', 'account', 2, '', {}, ''),
    (9, 8, 2, 'updated', 'account', 2, '', {'phone': _change('Phone', '021 555 0100', '082 555 0199')},
     'Account details'),
    (8, 16, 2, 'created', 'ticket', 1202, 'APS-1202 — Panels need cleaning', {}, ''),
    (7, 10, 1, 'created', 'ticket', 1203, 'APS-1203 — Geyser timer not switching off', {}, ''),
    (7, 10, 1, 'message_sent', 'ticket', 1203, 'APS-1203 — Geyser timer not switching off', {},
     'The geyser is on all day, our bill doubled this month.'),
    (6, 21, 0, 'password_changed', 'account', 0, '', {}, ''),
    (5, 12, 2, 'cancelled', 'subscription', 'SUB-88', 'Monthly solar panel cleaning', {},
     'Moving house at the end of the month.'),
    (4, 9, 1, 'updated', 'account', 1, '', {},
     'Asked to change the email to the new work address (waiting for the link to be clicked)'),
    (4, 10, 1, 'updated', 'account', 1, '', {'email': _change('Email', 'home@example.com', 'work@example.com')},
     'New email confirmed'),
    (3, 7, 0, 'created', 'site', 32, 'Warehouse — Bay 4', {}, ''),
    (2, 22, 2, 'password_reset', 'account', 2, '', {}, 'With an emailed link'),
    (2, 22, 2, 'signed_in', 'account', 2, '', {}, ''),
    (1, 9, 0, 'message_sent', 'ticket', 1201, 'APS-1201 — Inverter showing fault code F23', {},
     'All working again — thank you for the quick visit!'),
    (0, 8, 1, 'signed_in', 'account', 1, '', {}, ''),
    (0, 8, 1, 'created', 'ticket', 1204, 'APS-1204 — Battery not charging past 60%', {}, 'Urgent'),
]


class Command(BaseCommand):
    help = 'Add (or with --clear remove) demo audit history entries. Local only (DEBUG=True).'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Remove the demo entries instead.')
        parser.add_argument('--clients', action='store_true', help='Add client entries instead of staff ones.')

    def handle(self, *args, clear=False, clients=False, **options):
        if not settings.DEBUG:
            raise CommandError('Refusing: demo audit history is for local use only (DEBUG=True). '
                               'Audit history can never be deleted on a live server.')
        if clear:
            removed = self.clear()
            self.stdout.write(self.style.SUCCESS(f'Removed {removed} demo audit entries.'))
            return
        if clients:
            return self.seed_clients()

        people = list(User.objects.filter(is_active=True)
                      .filter(is_superuser=True).order_by('date_joined')[:1]) + \
            list(User.objects.filter(is_active=True, client_profile__role='admin', is_superuser=False)
                 .order_by('date_joined')[:2])
        if not people:
            raise CommandError('No staff accounts in this database to act as the demo people.')
        while len(people) < 3:
            people.append(people[0])

        now = timezone.localtime()
        rows = []
        for days, hour, who, action, kind, obj_id, name, changes, summary in STORY:
            actor = people[who] if who is not None else None
            when = (now - timedelta(days=days)).replace(hour=hour, minute=(obj_id * 7) % 60, second=0, microsecond=0)
            rows.append(AuditLog(
                created_at=min(when, now), actor=actor, actor_name=display_name(actor) if actor else '',
                actor_role=AuditLog.STAFF if actor else '',
                action=action, object_type=f'{DEMO_PREFIX}{kind}', object_id=str(obj_id), object_repr=name,
                changes=changes, summary=summary,
            ))
        AuditLog.objects.bulk_create(rows)
        self.stdout.write(self.style.SUCCESS(
            f'Added {len(rows)} demo audit entries as: {", ".join(sorted({display_name(p) for p in people}))}. '
            f'See /dashboard/audit/staff or /admin/records/auditlog/. Remove them with --clear.'))

    def seed_clients(self):
        people = list(User.objects.filter(is_active=True, client_profile__role='client')
                      .select_related('client_profile').order_by('date_joined')[:3])
        if not people:
            raise CommandError('No client accounts in this database to act as the demo clients.')
        while len(people) < 3:
            people.append(people[0])

        now = timezone.localtime()
        rows = []
        for n, (days, hour, who, action, kind, obj_id, name, changes, summary) in enumerate(CLIENT_STORY):
            actor = people[who]
            if kind == 'account':  # the subject is the client's own login
                obj_id, name = actor.pk, f'{display_name(actor)} ({actor.username})'
            when = (now - timedelta(days=days)).replace(hour=hour, minute=(n * 7) % 60, second=0, microsecond=0)
            rows.append(AuditLog(
                created_at=min(when, now), actor=actor, actor_name=display_name(actor), actor_role=AuditLog.CLIENT,
                action=action, object_type=f'{DEMO_PREFIX}{kind}', object_id=str(obj_id), object_repr=name,
                changes=changes, summary=summary,
            ))
        AuditLog.objects.bulk_create(rows)
        self.stdout.write(self.style.SUCCESS(
            f'Added {len(rows)} demo client audit entries as: '
            f'{", ".join(sorted({display_name(p) for p in people}))}. See /dashboard/audit/clients.'))

    @staticmethod
    @transaction.atomic
    def clear():
        """Delete only the demo rows. The append-only trigger is off just for this one statement."""
        table = AuditLog._meta.db_table
        with connection.cursor() as cur:
            pg = connection.vendor == 'postgresql'
            if pg:
                cur.execute(f'ALTER TABLE {table} DISABLE TRIGGER records_auditlog_append_only')
            cur.execute(f'DELETE FROM {table} WHERE object_type LIKE %s', [f'{DEMO_PREFIX}%'])
            removed = cur.rowcount
            if pg:
                cur.execute(f'ALTER TABLE {table} ENABLE TRIGGER records_auditlog_append_only')
        return removed
