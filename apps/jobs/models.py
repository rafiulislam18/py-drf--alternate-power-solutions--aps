"""
Operations jobs — the dashboard's replacement for the "Jobs" tab of the APS
Operations spreadsheet (Apps Script: 00_Config, 03_Escalations, 07_Triggers_Menu).

- Every job gets the next number for good (``APS-001`` …): a number in the
  trash stays taken, so a restore never clashes and numbers are never reused.
- Status rules from the sheet: DONE / INVOICED stamps today's completion date
  (only if it's empty, so DONE → INVOICED keeps the first date); any other
  status clears it and the unpaid-alert record.
- ``last_updated`` moves on ANY change to the job (a field, the assigned
  staff, a new or edited comment) — not on a save that changed nothing.
- Rows are coloured by days since ``last_updated`` while a job is live
  (open, quote sent, in progress): watch / aging / critical from
  ``JobSettings`` (7 / 14 / 21 days by default, like the sheet's Settings tab).
- Categories are an editable list (``JobCategory``): added, renamed or
  switched off on the dashboard, never deleted (jobs keep pointing at them).
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.records.models import TrashableModel


class JobCategory(models.Model):
    """A job category ("Call out", "Electrical", …). Switched off = hidden
    from new jobs and the filters' defaults, but jobs that use it keep it."""

    audit_section = 'jobs'

    name = models.CharField(max_length=60, unique=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField('in use', default=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'job categories'

    def __str__(self):
        return self.name


class Job(TrashableModel):
    # In workflow order: the list's default sort (live work first).
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        QUOTE_SENT = 'quote_sent', 'Quote sent'
        IN_PROGRESS = 'in_progress', 'In progress'
        ON_HOLD = 'on_hold', 'On hold'
        DONE = 'done', 'Done'
        INVOICED = 'invoiced', 'Invoiced'
        NOT_ACCEPTED = 'not_accepted', 'Not accepted'
        CANCELLED = 'cancelled', 'Cancelled'

    class Payment(models.TextChoices):
        PAID = 'paid', 'Paid'
        DEPOSIT = 'deposit', 'Paid deposit'
        UNPAID = 'unpaid', 'Unpaid'
        NO_CHARGE = 'no_charge', 'No charge'

    # Nothing more to collect.
    SETTLED = (Payment.PAID, Payment.NO_CHARGE)

    FINISHED = (Status.DONE, Status.INVOICED)
    # Only these get the age colours; the rest are parked or closed.
    LIVE = (Status.OPEN, Status.QUOTE_SENT, Status.IN_PROGRESS)

    number = models.PositiveIntegerField(unique=True, editable=False)
    category = models.ForeignKey(JobCategory, on_delete=models.PROTECT, related_name='jobs')
    client_name = models.CharField('client / job', max_length=200, blank=True)
    # Optional links to a dashboard client account and one of its sites.
    client = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='operations_jobs', verbose_name='linked client')
    site = models.ForeignKey('solar_dashboard.Site', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='operations_jobs', verbose_name='linked site')
    task = models.TextField('task / description', blank=True)
    call_date = models.DateField(null=True, blank=True)
    assessment = models.DateField('assessment', null=True, blank=True)
    follow_up_date = models.DateField('follow-up date', null=True, blank=True)
    scheduled_date = models.DateField(null=True, blank=True)
    scheduled_time = models.CharField('time', max_length=50, blank=True)  # free text: "10h00 - 10h30", "Anytime"
    coc_issued = models.DateField('COC issued', null=True, blank=True)
    invoice = models.CharField('invoiced', max_length=100, blank=True)  # invoice no. or a note: "INV-02414", "No charge"
    assigned_to = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='assigned_jobs')
    # Blank = no status yet (old sheet rows); listed last and never coloured.
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, blank=True, db_index=True)
    payment = models.CharField('payment status', max_length=10, choices=Payment.choices, blank=True, db_index=True)
    completion_date = models.DateField(null=True, blank=True)
    # Empty only on jobs imported from the sheet that never had one.
    date_added = models.DateTimeField(default=timezone.now, null=True, blank=True, db_index=True)
    last_updated = models.DateTimeField(default=timezone.now, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', editable=False)
    # When the daily alerts last emailed about this job (task 5).
    unpaid_alert_at = models.DateTimeField(null=True, blank=True, editable=False)
    stale_alert_at = models.DateTimeField(null=True, blank=True, editable=False)
    # Brought in from the Operations sheet by ``manage.py import_jobs_sheet``.
    imported_from_sheet = models.BooleanField(default=False, editable=False)

    trash_section = 'jobs'
    trash_label = 'Job'
    # Stamps the system moves on every change / alert — not something people edit.
    audit_exclude = {'number', 'date_added', 'last_updated', 'created_by', 'unpaid_alert_at', 'stale_alert_at',
                     'imported_from_sheet'}

    class Meta:
        ordering = [models.F('date_added').desc(nulls_last=True), '-last_updated', '-number']

    @property
    def job_id(self):
        return format_job_id(self.number)

    def __str__(self):
        name = self.client_name or ' '.join(self.task.split())[:60] or '(no client)'
        return f'{self.job_id} · {name}'

    def trash_preview(self, request):
        return {
            'kind': 'job',
            'job_id': self.job_id,
            'category': self.category.name,
            'client_name': self.client_name,
            'task': self.task,
            'status': self.status,
            'status_label': self.get_status_display(),
            'payment': self.payment,
            'payment_label': self.get_payment_display() if self.payment else '',
            'assigned_to': [staff_name(u) for u in self.assigned_to.all()],
            'date_added': self.date_added,
            'last_updated': self.last_updated,
            'comments': [{'author': c.author_name, 'body': c.body, 'created_at': c.created_at}
                         for c in self.comments.order_by('-created_at')[:5]],
            'comment_count': self.comments.count(),
        }


class JobComment(models.Model):
    """One comment on a job. Comments are never deleted (the author can edit
    their own); they go with the job when it is deleted for good."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+')
    author_name = models.CharField(max_length=200, blank=True)  # as it was then; '' = the system
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.job.job_id}: {self.body[:40]}'


class JobSettings(models.Model):
    """Days without an update before a live job turns watch / aging / critical
    (the sheet's Settings tab). One row (pk=1)."""

    audit_section = 'jobs'

    watch_days = models.PositiveSmallIntegerField(default=7)
    aging_days = models.PositiveSmallIntegerField(default=14)
    critical_days = models.PositiveSmallIntegerField(default=21)

    class Meta:
        verbose_name = 'job settings'
        verbose_name_plural = 'job settings'

    def __str__(self):
        return 'Job settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


def format_job_id(number):
    return f'APS-{number:03d}'


def staff_name(user):
    return user.get_full_name() or user.username
