"""
Demo jobs for showing the Jobs page: ~30 jobs across every status, category,
payment and age colour, with comments and the technicians assigned.

    python manage.py seed_jobs_demo           # add the demo jobs
    python manage.py seed_jobs_demo --clear   # remove them again (and the demo people)

The people are demo staff accounts (usernames ``demo.<name>``, no password,
no permissions — they can't sign in). Demo jobs are the ones a demo account
created, so ``--clear`` removes exactly what this command added and frees
their APS numbers again.

Job history (audit entries) is only written locally (DEBUG=True): audit history
can never be deleted on a live server, so there the jobs come without it.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.core.models import ClientProfile
from apps.jobs.models import Job, JobCategory, JobComment, staff_name
from apps.jobs.services import next_number
from apps.records.audit import change
from apps.records.models import AuditLog, display_name
from apps.staff.models import StaffAccess

PREFIX = 'demo.'
OFFICE = 'tenielle'
PEOPLE = [OFFICE, 'lerenzo', 'brian', 'lucky', 'gerswin', 'emanual', 'mahdee']

S = Job.Status
P = Job.Payment

# client, task, category, status, payment, assigned, days since added, days since last update,
# extras {call, assess, sched, time, invoice, coc, follow}: days ago (+ = past, - = future) or text,
# comments [(who, days ago, text)]
JOBS = [
    ('Harbour View Body Corporate', 'Inverter tripping every afternoon when the lift runs', 'Call out', S.INVOICED, P.PAID,
     ['lerenzo'], 44, 30, {'call': 44, 'sched': 41, 'time': '09h00', 'invoice': 'INV-02381'},
     [('lerenzo', 41, 'Lift motor surge trips the inverter. Fitted a soft starter.'), (OFFICE, 30, 'Paid in full.')]),
    ('Kloof Street Studios', 'Install dedicated plugs in studio and reception', 'Electrical', S.INVOICED, P.PAID,
     ['brian', 'lucky'], 42, 28, {'call': 42, 'assess': 40, 'sched': 36, 'time': '10h00 - 10h30', 'invoice': 'INV-02390',
                                   'coc': 35},
     [('brian', 36, 'Done, 6 plugs on a new circuit. COC issued.')]),
    ('Fernwood Primary School', 'Quote for new power supply to gate motor', 'Open jobs', S.NOT_ACCEPTED, '',
     ['gerswin'], 40, 26, {'call': 40, 'assess': 37},
     [('gerswin', 37, 'Needs a 40 m trench, quoted R 18 400.'), (OFFICE, 26, 'School went with another supplier.')]),
    ('Riedwaan Jacobs – Rondebosch', 'E.L.U tripping, check earth leakage on geyser circuit', 'Call out', S.INVOICED,
     P.PAID, ['mahdee'], 38, 25, {'call': 38, 'sched': 37, 'time': 'Anytime', 'invoice': 'INV-02402'},
     [('mahdee', 37, 'Geyser element leaking to earth. Replaced element.')]),
    ('Urban Growth – Rialto', 'Monthly maintenance: clean panels, check inverter logs', 'Urban Growth', S.INVOICED,
     P.PAID, ['lucky', 'mahdee'], 36, 22, {'sched': 33, 'invoice': 'INV-02411'}, []),
    ('Constantia Wine Estate', 'Install 2 x 5.12 kWh batteries on existing inverter', 'Inverter installation', S.DONE,
     P.UNPAID, ['lerenzo', 'emanual'], 34, 12, {'call': 34, 'assess': 31, 'sched': 14, 'time': '08h00', 'coc': 13,
                                                'invoice': 'INV-02414'},
     [('lerenzo', 14, 'Batteries in and commissioned. Client happy.'),
      (OFFICE, 12, 'Invoice sent, waiting for payment.')]),
    ('Bayside Storage (Pty) Ltd', 'DB board upgrade and surge protection', 'Electrical', S.DONE, P.DEPOSIT,
     ['brian'], 30, 9, {'call': 30, 'assess': 28, 'sched': 11, 'time': '07h30', 'invoice': 'INV-02417'},
     [('brian', 11, 'New board fitted, surge arrestors on both phases.'), (OFFICE, 9, 'Deposit received, balance due.')]),
    ('Sea Point Medical Rooms', 'COC for sale of property', "COC's", S.INVOICED, P.PAID, ['gerswin'], 29, 18,
     {'call': 29, 'sched': 24, 'coc': 22, 'invoice': 'INV-02405'},
     [('gerswin', 24, 'Two faults fixed (no earth on 2 plugs) then COC issued.')]),
    ('Ayesha Khan – Lansdowne', 'Inverter beeping, battery not charging past 60%', 'Fault finding', S.INVOICED, P.PAID,
     ['mahdee'], 27, 20, {'call': 27, 'sched': 26, 'time': '14h00', 'invoice': 'NO CHARGE'},
     [('mahdee', 26, 'Firmware update + BMS settings. Under warranty, no charge.')]),
    ('Pinelands Retirement Village', 'Annual maintenance on 3 inverter sites', 'Maintenance', S.ON_HOLD, '',
     ['lucky'], 26, 19, {'call': 26, 'follow': -3},
     [('lucky', 19, 'Waiting for the trustees to approve access to block C.')]),
    ('Mark Daniels – Tokai', 'Quote: 8 kW hybrid system with 10 kWh battery', 'Open jobs', S.QUOTE_SENT, '',
     ['emanual'], 24, 8, {'call': 24, 'assess': 21, 'follow': -2},
     [('emanual', 21, 'Roof faces north, room for 14 panels.'), (OFFICE, 8, 'Quote emailed, follow up Thursday.')]),
    ('Green Point Apartments', 'Common-area lights not switching on (timer)', 'Call out', S.CANCELLED, '',
     [], 23, 21, {'call': 23}, [(OFFICE, 21, 'Building manager fixed the timer themselves.')]),
    ('Urban Growth – Paarl', 'Replace failed MPPT module on inverter 2', 'Urban Growth', S.IN_PROGRESS, '',
     ['lerenzo'], 22, 16, {'call': 22, 'assess': 20, 'sched': 5, 'time': '09h00 - 12h00'},
     [('lerenzo', 16, 'Part ordered from supplier, ETA 2 weeks.')]),
    ('Woodstock Bakery', 'Oven circuit keeps tripping', 'Call out', S.INVOICED, P.PAID, ['brian'], 20, 13,
     {'call': 20, 'sched': 19, 'time': '06h00', 'invoice': 'INV-02409'}, []),
    ('Chantel Adams – Bergvliet', 'Install backup for lights and Wi-Fi only', 'Inverter installation', S.OPEN, '',
     [], 25, 25, {'call': 25}, []),
    ('Muizenberg Surf Club', 'Solar geyser not heating', 'Call out', S.OPEN, '', ['gerswin'], 17, 17,
     {'call': 17}, [('gerswin', 17, 'Need to get onto the roof, waiting for a dry day.')]),
    ('Claremont Dental Practice', 'SCADA monitoring not reporting since load shedding', 'SCADA', S.IN_PROGRESS, '',
     ['emanual'], 15, 15, {'call': 15, 'sched': 14},
     [('emanual', 15, 'Router replaced, logger still offline. Checking the comms card.')]),
    ('Thandi Mokoena – Observatory', 'Assessment for full house backup', 'Assessment', S.OPEN, '', ['lucky'], 10, 10,
     {'call': 10, 'assess': -1}, []),
    ('Vredehoek Townhouses', 'Gate motor battery and charger', 'Call out', S.DONE, P.UNPAID, ['mahdee'], 12, 10,
     {'call': 12, 'sched': 10, 'time': '11h00', 'invoice': 'INV-02420'}, []),
    ('Rondebosch Cricket Club', 'Floodlight contactor replacement', 'Electrical', S.QUOTE_SENT, '', ['brian'], 9, 4,
     {'call': 9, 'assess': 6}, [(OFFICE, 4, 'Quote sent: R 6 850 incl. VAT.')]),
    ('Fish Hoek Library', 'COC after renovation', "COC's", S.IN_PROGRESS, '', ['gerswin', 'lucky'], 7, 2,
     {'call': 7, 'sched': 2, 'time': '08h30'}, [('gerswin', 2, 'Half done, back tomorrow for the upstairs.')]),
    ('Lerato Dlamini – Wynberg', 'Plugs in kitchen dead after storm', 'Call out', S.OPEN, '', ['mahdee'], 3, 3,
     {'call': 3, 'sched': -1, 'time': 'After 12h00'}, []),
    ('Bayside Storage (Pty) Ltd', 'Add 4 panels to the warehouse array', 'Open jobs', S.OPEN, '', [], 2, 2,
     {'call': 2}, []),
    ('Kenilworth Guest House', 'Inverter showing fault code F23', 'Call out', S.OPEN, '', ['lerenzo'], 1, 1,
     {'call': 1, 'sched': -1, 'time': '10h00'}, [(OFFICE, 1, 'Client sent photos on WhatsApp.')]),
    ('MLP – Bellville office', 'Quarterly inspection', 'MLP', S.INVOICED, P.PAID, ['emanual'], 33, 27,
     {'sched': 30, 'invoice': 'APS-2031'}, []),
    ('MLP – Durbanville office', 'Replace 2 faulty panels under warranty', 'MLP', S.DONE, '', ['emanual', 'lerenzo'],
     19, 6, {'sched': 7, 'invoice': 'NO BILLING'}, [('emanual', 7, 'Warranty swap, supplier credit note to follow.')]),
    ('Hout Bay Harbour Café', 'Fridge circuit overloaded, add second circuit', 'Electrical', S.OPEN, '', ['brian'],
     8, 8, {'call': 8}, []),
    ('Tamboerskloof Residence', 'Assessment: move inverter to garage', 'Assessment', S.NOT_ACCEPTED, '', ['lucky'],
     16, 11, {'call': 16, 'assess': 13}, [(OFFICE, 11, 'Client decided to wait until next year.')]),
    ('APS workshop', 'Fix workshop compressor socket', 'Other', S.DONE, '', ['mahdee'], 5, 4,
     {'sched': 4, 'invoice': 'PROJECT'}, []),
]


def _ago(days, hour=9):
    day = timezone.localdate() - timedelta(days=days)
    return timezone.make_aware(datetime.combine(day, time(hour, (days * 7) % 60)))


class Command(BaseCommand):
    help = 'Add (or with --clear remove) demo jobs for showing the Jobs page.'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Remove the demo jobs and demo people instead.')

    def handle(self, *args, clear=False, **options):
        if clear:
            jobs, people, history = self.clear()
            self.stdout.write(self.style.SUCCESS(
                f'Removed {jobs} demo jobs, {people} demo people and {history} demo history entries.'))
            return
        if Job.all_objects.filter(created_by__username__startswith=PREFIX).exists():
            raise CommandError('The demo jobs are already there. Run with --clear first to add them again.')
        missing = {j[2] for j in JOBS} - set(JobCategory.objects.values_list('name', flat=True))
        if missing:
            raise CommandError(f'Missing job categories (run migrate): {", ".join(sorted(missing))}')
        added, history = self.seed()
        self.stdout.write(self.style.SUCCESS(
            f'Added {added} demo jobs ({format_range(added)})'
            + (f' with {history} history entries' if history else ' (no job history: live server)')
            + '. Remove them with --clear.'))

    @transaction.atomic
    def seed(self):
        people = {name: self.person(name) for name in PEOPLE}
        office = people[OFFICE]
        categories = {c.name: c for c in JobCategory.objects.all()}
        # Two jobs get linked to a real client + site, if this database has one.
        profile = ClientProfile.objects.filter(role='client', user__is_active=True, user__sites__is_active=True) \
            .select_related('user').first()
        site = profile.user.sites.filter(is_active=True).first() if profile else None
        now = timezone.now()
        log = settings.DEBUG
        history = []

        rows = sorted(JOBS, key=lambda j: -j[6])  # oldest first, so the numbers run in date order
        start = next_number()
        for i, (client, task, category, status, payment, assigned, added, updated, extra, comments) in enumerate(rows):
            def day(key):
                v = extra.get(key)
                return None if v is None else timezone.localdate() - timedelta(days=v)

            job = Job.objects.create(
                number=start + i, category=categories[category], client_name=client, task=task, status=status,
                payment=payment, call_date=day('call'), assessment=day('assess'), scheduled_date=day('sched'),
                scheduled_time=extra.get('time', ''), invoice=extra.get('invoice', ''), coc_issued=day('coc'),
                follow_up_date=day('follow'),
                completion_date=timezone.localdate() - timedelta(days=updated) if status in Job.FINISHED else None,
                created_by=office, date_added=_ago(added, 8),
            )
            if site and i in (3, len(rows) - 3):
                job.client, job.site = profile.user, site
                job.save(update_fields=['client', 'site'])
            team = [people[n] for n in assigned]
            job.assigned_to.set(team)
            for who, days, body in comments:
                JobComment.objects.create(job=job, author=people[who], author_name=staff_name(people[who]), body=body,
                                          created_at=min(_ago(days, 15), now))
            Job.objects.filter(pk=job.pk).update(last_updated=min(_ago(updated, 16), now))

            if log:
                history.append(self.entry(office, AuditLog.CREATED, job, _ago(added, 8),
                                          summary=f'Assigned to {"; ".join(staff_name(p) for p in team)}' if team else ''))
                for who, days, body in comments:
                    history.append(self.entry(people[who], 'commented', job, min(_ago(days, 15), now), summary=body))
                if status != S.OPEN:
                    changes = {'status': change('Status', 'Open', job.get_status_display())}
                    if job.completion_date:
                        changes['completion_date'] = change('Completion date', '', f'{job.completion_date:%d %b %Y}')
                    if payment:
                        changes['payment'] = change('Payment status', '', job.get_payment_display())
                    history.append(self.entry(team[0] if team else office, AuditLog.UPDATED, job,
                                              min(_ago(updated, 16), now), changes=changes))
        if history:
            AuditLog.objects.bulk_create(history)
        return len(rows), len(history)

    @staticmethod
    def person(name):
        user, created = User.objects.get_or_create(username=f'{PREFIX}{name}',
                                                   defaults={'first_name': name.capitalize()})
        if created:
            user.set_unusable_password()  # can't sign in
            user.save(update_fields=['password'])
            ClientProfile.objects.create(user=user, role='admin')
            StaffAccess.objects.create(user=user, permissions=[])
        return user

    @staticmethod
    def entry(actor, action, job, when, changes=None, summary=''):
        return AuditLog(created_at=when, actor=actor, actor_name=display_name(actor), actor_role=AuditLog.STAFF,
                        action=action, object_type=job._meta.label_lower, object_id=str(job.pk),
                        object_repr=str(job)[:300], section='jobs', changes=changes or {}, summary=summary[:500])

    @staticmethod
    @transaction.atomic
    def clear():
        jobs = list(Job.all_objects.filter(created_by__username__startswith=PREFIX).values_list('pk', flat=True))
        history = 0
        if jobs and settings.DEBUG:  # only local runs wrote history for them
            table = AuditLog._meta.db_table
            ids = [str(pk) for pk in jobs]
            with connection.cursor() as cur:
                pg = connection.vendor == 'postgresql'
                if pg:
                    cur.execute(f'ALTER TABLE {table} DISABLE TRIGGER records_auditlog_append_only')
                cur.execute(f"DELETE FROM {table} WHERE object_type = 'jobs.job' AND object_id = ANY(%s)"
                            if pg else
                            f"DELETE FROM {table} WHERE object_type = 'jobs.job' AND object_id IN "
                            f"({', '.join(['%s'] * len(ids))})", [ids] if pg else ids)
                history = cur.rowcount
                if pg:
                    cur.execute(f'ALTER TABLE {table} ENABLE TRIGGER records_auditlog_append_only')
        Job.all_objects.filter(pk__in=jobs).delete()
        people = User.objects.filter(username__startswith=PREFIX, client_profile__role='admin').delete()[1].get(
            'auth.User', 0)
        return len(jobs), people, history


def format_range(count):
    last = next_number() - 1
    return f'APS-{last - count + 1:03d} to APS-{last:03d}'
