"""
Client tickets — job requests clients raise from the APS dashboard.

A ticket belongs to a client account (one dashboard login per client company,
``auth.User`` with a ClientProfile) and one of that client's sites
(``solar_dashboard.Site`` — the same site list the solar reports use).

The old invite-only portal models (company, contacts, email sign-in codes and
its own site list) were folded into dashboard accounts in migration 0004.
"""

import os
import uuid

from django.contrib.auth.models import User
from django.db import models

# Ticket references are shown as "APS-<offset + pk>" so the first ticket reads
# APS-1001 rather than APS-1. Derived from the pk, so there's no counter to race.
TICKET_NUMBER_OFFSET = 1000
TICKET_PREFIX = 'APS-'


class Ticket(models.Model):
    """A job request raised by a client account."""

    class Urgency(models.TextChoices):
        NORMAL = 'normal', 'Normal'
        URGENT = 'urgent', 'Urgent'
        EMERGENCY = 'emergency', 'Emergency'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        VISIT_BOOKED = 'visit_booked', 'Visit booked'
        ON_SITE = 'on_site', 'On site'
        IN_PROGRESS = 'in_progress', 'In progress'
        QUOTE_TO_APPROVE = 'quote_to_approve', 'Quote to approve'
        INFO_NEEDED = 'info_needed', 'Info needed'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    # The four buckets the portal's stat cards and tabs are built on. Every
    # status sits in exactly one group.
    STATUS_GROUPS = {
        'open': [Status.OPEN],
        'in_progress': [Status.VISIT_BOOKED, Status.ON_SITE, Status.IN_PROGRESS],
        'waiting': [Status.QUOTE_TO_APPROVE, Status.INFO_NEEDED],
        'completed': [Status.COMPLETED, Status.CANCELLED],
    }

    client = models.ForeignKey(User, on_delete=models.PROTECT, related_name='tickets')
    site = models.ForeignKey('solar_dashboard.Site', on_delete=models.PROTECT, related_name='tickets')

    title = models.CharField(max_length=200)
    description = models.TextField()
    # The name of the APS service the client chose. The choices come live
    # from Site content → Services (validated when the ticket is raised); the
    # name is stored as text, with no link to that table, so the ticket keeps
    # reading right if a service is later renamed or removed.
    service = models.CharField(
        max_length=255,
        help_text='The APS service chosen when the ticket was raised (from Site content → Services).',
    )
    urgency = models.CharField(max_length=10, choices=Urgency.choices, default=Urgency.NORMAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    preferred_visit_date = models.DateField(null=True, blank=True)
    site_contact_name = models.CharField(max_length=150, blank=True)
    site_contact_phone = models.CharField(max_length=30, blank=True)
    technician_name = models.CharField(
        max_length=150,
        blank=True,
        help_text='Shown to the client. Leave blank until someone is assigned.',
    )

    # Chat read markers: the id of the newest message each side has seen. Staff
    # share one marker (the team reads a ticket together). Drives the unread
    # badges and the "Seen" mark.
    client_read_upto = models.PositiveBigIntegerField(default=0, editable=False)
    staff_read_upto = models.PositiveBigIntegerField(default=0, editable=False)
    # The newest message already covered by an unread-messages email to each
    # side (see apps.client_portal.digests), so nothing is emailed twice.
    client_emailed_upto = models.PositiveBigIntegerField(default=0, editable=False)
    staff_emailed_upto = models.PositiveBigIntegerField(default=0, editable=False)

    # New-ticket emails still to send (see apps.client_portal.alerts): set when
    # a normal/urgent ticket is raised and cleared by the 30-minute job.
    # Emergencies are emailed at once and never set these.
    team_alert_pending = models.BooleanField(default=False, editable=False)
    client_alert_pending = models.BooleanField(default=False, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        indexes = [
            models.Index(fields=['client', 'status'], name='ticket_client_status_idx'),
            models.Index(fields=['client', '-updated_at'], name='ticket_client_updated_idx'),
        ]

    def __str__(self):
        return f'{self.reference} — {self.title}'

    @property
    def reference(self):
        return f'{TICKET_PREFIX}{TICKET_NUMBER_OFFSET + self.pk}' if self.pk else ''

    @property
    def status_group(self):
        for group, statuses in self.STATUS_GROUPS.items():
            if self.status in statuses:
                return group
        return 'open'

    @classmethod
    def pk_from_reference(cls, text):
        """``"APS-1045"`` / ``"aps1045"`` / ``"1045"`` → 45, else ``None``."""
        raw = (text or '').strip().upper().replace(' ', '')
        if raw.startswith(TICKET_PREFIX):
            raw = raw[len(TICKET_PREFIX):]
        elif raw.startswith(TICKET_PREFIX.rstrip('-')):
            raw = raw[len(TICKET_PREFIX) - 1:]
        # isascii(): str.isdigit() also accepts "²" etc., which int() rejects.
        # The length cap keeps the pk inside the DB's integer range.
        if not (raw.isascii() and raw.isdigit()) or len(raw) > 9:
            return None
        pk = int(raw) - TICKET_NUMBER_OFFSET
        return pk if pk > 0 else None


def _attachment_path(instance, filename):
    # Random filename: attachments are client-confidential, so their media URLs
    # mustn't be guessable from the ticket number or original name.
    ext = os.path.splitext(filename)[1].lower()
    return f'client_portal/tickets/{instance.ticket_id}/{uuid.uuid4().hex}{ext}'


class TicketAttachment(models.Model):
    """A photo or document uploaded with a ticket."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=_attachment_path)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return self.original_name


# Longest chat message accepted (characters).
MESSAGE_MAX_LENGTH = 4000


class TicketMessage(models.Model):
    """One chat message on a ticket, from the client or from APS staff."""

    class Author(models.TextChoices):
        CLIENT = 'client', 'Client'
        STAFF = 'staff', 'APS'

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='ticket_messages')
    author_role = models.CharField(max_length=10, choices=Author.choices)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['ticket', 'id'], name='ticket_message_ticket_id_idx')]

    def __str__(self):
        return f'{self.ticket.reference} — {self.get_author_role_display()} — {self.body[:40]}'
