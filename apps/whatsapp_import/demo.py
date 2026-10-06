"""
Demo WhatsApp messages for showing the review page before real exports exist
(``manage.py seed_whatsapp_demo``).

Every demo message hangs off one ImportedFile whose Drive id is
:data:`DEMO_FILE_ID`, so ``--clear`` removes them all in one go (cascade), and
the jobs export skips them (``jobs_export.pending_jobs_qs``) — ticking "job"
on a demo message never reaches the real jobs sheet.

Names, numbers and addresses are made up. Times are relative to "now", so the
page always looks current after a ``--reset``.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from .models import ImportedFile, WhatsAppMessage

DEMO_FILE_ID = 'demo-seed'
_SAST = ZoneInfo('Africa/Johannesburg')

JOBS_GROUP = 'APS Jobs & Call-outs'
SEAVIEW = 'Seaview Estates – Maintenance'
KENILWORTH = 'Kenilworth Court Body Corporate'

# (days ago, "HH:MM", chat, sender, text, state) — state: '' pending, 'job'
# (already flagged and sent to the sheet), 'dismissed'.
MESSAGES = [
    (9, '07:42', JOBS_GROUP, 'Megan Botha', 'Morning all, inverter at 14 Wolfe St Wynberg showing "Grid lost" alarm since the power came back. Battery at 18%. Can someone check today?', 'job'),
    (9, '07:44', JOBS_GROUP, 'APS Office', 'Noted Megan, will get a tech out this morning.', 'dismissed'),
    (9, '11:15', SEAVIEW, 'Thandi (Seaview Estates)', 'Hi APS, unit 4B has a burst geyser, water coming through the ceiling of 3B. Tenant has switched the isolator off. Urgent please!', 'job'),
    (9, '11:16', SEAVIEW, 'Thandi (Seaview Estates)', 'Photo sent to your email as well', ''),
    (8, '08:05', KENILWORTH, 'Pieter van Wyk', 'Good morning. The DB board in the common area keeps tripping when the gate motor runs. Can you quote to find the fault?', 'job'),
    (8, '08:30', JOBS_GROUP, 'Sipho Ndlovu', 'Solar panels at Constantia Heights need cleaning, the client says output is down about 20% since the fires. 24 panels.', ''),
    (8, '12:02', JOBS_GROUP, 'Aisha Adams', 'Lunch at 1?', 'dismissed'),
    (7, '09:18', SEAVIEW, 'Thandi (Seaview Estates)', 'Thanks for sorting the geyser so quickly yesterday. Tenant is very happy \U0001F64F', 'dismissed'),
    (7, '10:47', '+27 82 555 0143', '+27 82 555 0143', 'Hi, got your number from a neighbour. Looking for a quote for a 5 kW inverter and battery backup for a 3 bedroom house in Plumstead. Can someone come look?', ''),
    (7, '14:20', KENILWORTH, 'Pieter van Wyk', 'Also need a CoC for the electrical installation, we are selling unit 12. Transfer is end of the month.', 'job'),
    (6, '07:55', JOBS_GROUP, 'Megan Botha', 'Tech confirmed the Wolfe St inverter is back online, it was the grid relay. Closing that one.', ''),
    (6, '09:40', SEAVIEW, 'Riaan (Seaview Estates)', 'Can we get the outside walls of Block C painted before December? About 400 m², needs waterproofing on the parapet walls too.', ''),
    (6, '16:05', '+27 71 555 0198', '+27 71 555 0198', 'Do you install EV chargers? Just bought a car and need a 7 kW wall box in the garage in Rondebosch.', ''),
    (5, '08:12', JOBS_GROUP, 'Sipho Ndlovu', 'Constantia Heights cleaning booked for Thursday 08:00.', ''),
    (5, '10:33', KENILWORTH, 'Pieter van Wyk', 'The pool pump isn’t switching on since the storm, might be the timer or the pump itself. Please have a look when you’re here for the DB board.', ''),
    (5, '15:48', JOBS_GROUP, 'Aisha Adams', 'Reminder: toolbox talk tomorrow 07:30 at the office.', 'dismissed'),
    (4, '07:20', SEAVIEW, 'Thandi (Seaview Estates)', 'Unit 7A reports a strong burning smell from the plug in the kitchen and the lights flickering. Tenant has switched off at the main.', ''),
    (4, '07:21', SEAVIEW, 'Thandi (Seaview Estates)', 'She’s home all day today', ''),
    (4, '11:02', '+27 83 555 0127', '+27 83 555 0127', 'Good day, our roof in Kenilworth leaks every time it rains, water stains in two bedrooms. Do you do roof repairs and waterproofing?', ''),
    (4, '13:40', JOBS_GROUP, 'Megan Botha', 'Anyone have a spare 32A breaker in the bakkie?', ''),
    (3, '08:15', KENILWORTH, 'Pieter van Wyk', 'Thanks for the quote on the DB board. Approved, please go ahead. When can you start?', ''),
    (3, '09:05', JOBS_GROUP, 'Sipho Ndlovu', 'Client in Tokai wants their Sunsynk inverter firmware updated and the settings checked, batteries not charging fully from solar.', ''),
    (3, '14:30', SEAVIEW, 'Riaan (Seaview Estates)', 'Just following up on the Block C painting quote?', ''),
    (2, '07:50', '+27 82 555 0143', '+27 82 555 0143', 'Hi, just checking if someone can come Saturday for the inverter quote in Plumstead? Weekdays I’m at work.', ''),
    (2, '10:10', JOBS_GROUP, 'Aisha Adams', 'Blocked drain at the Claremont office park, water backing up in the ground floor bathrooms. Client asked for a plumber today if possible.', ''),
    (2, '12:45', SEAVIEW, 'Thandi (Seaview Estates)', 'Unit 2C tenant says the shower mixer is dripping non-stop and the water bill is huge. Not urgent but please schedule.', ''),
    (1, '06:58', JOBS_GROUP, 'Megan Botha', 'Heads up: load-shedding stage 4 from 16:00 today. Expect inverter calls.', ''),
    (1, '16:22', '+27 64 555 0176', '+27 64 555 0176', 'Our inverter is beeping and says "Battery low" even though load-shedding only started an hour ago. Deye 8 kW. Please call me back.', ''),
    (1, '16:40', KENILWORTH, 'Pieter van Wyk', 'Gate motor battery seems dead again, gate won’t open during load-shedding. Residents are stuck.', ''),
    (0, '07:35', SEAVIEW, 'Thandi (Seaview Estates)', 'Good morning, the smell in 7A is back after the repair. Can the tech come back today please?', ''),
    (0, '08:20', JOBS_GROUP, 'Sipho Ndlovu', 'Thumbs up', ''),
    (0, '09:05', '+27 71 555 0198', '+27 71 555 0198', 'Hi again, any news on the EV charger quote? Happy to send photos of the DB board.', ''),
]


def _sent_at(days_ago, hhmm, now):
    hour, minute = (int(x) for x in hhmm.split(':'))
    day = timezone.localtime(now, _SAST) - timedelta(days=days_ago)
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def demo_exists():
    return ImportedFile.objects.filter(drive_file_id=DEMO_FILE_ID).exists()


@transaction.atomic
def clear_demo():
    """Remove every demo message. Returns how many were removed."""
    count = WhatsAppMessage.objects.filter(source_file__drive_file_id=DEMO_FILE_ID).count()
    ImportedFile.objects.filter(drive_file_id=DEMO_FILE_ID).delete()  # cascades to the messages
    return count


@transaction.atomic
def seed_demo(now=None):
    """Create the demo messages (dated relative to ``now``). Returns how many."""
    now = now or timezone.now()
    source = ImportedFile.objects.create(
        drive_file_id=DEMO_FILE_ID, name='Demo data (not a real export)', message_count=len(MESSAGES),
    )
    rows = []
    for days_ago, hhmm, chat, sender, text, state in MESSAGES:
        sent = _sent_at(days_ago, hhmm, now)
        if sent > now:  # today's later times haven't happened yet
            sent = now - timedelta(minutes=5)
        rows.append(WhatsAppMessage(
            sender=sender, sent_at=sent, text=text, chat_name=chat, source_file=source,
            marked_as_job=state == 'job', marked_as_job_at=sent if state == 'job' else None,
            # Demo jobs show as already sent, so nothing demo is ever pending export.
            exported_to_jobs_sheet=state == 'job', exported_to_jobs_sheet_at=sent if state == 'job' else None,
            dismissed=state == 'dismissed', dismissed_at=sent if state == 'dismissed' else None,
        ))
    WhatsAppMessage.objects.bulk_create(rows, ignore_conflicts=True)
    return WhatsAppMessage.objects.filter(source_file=source).count()
