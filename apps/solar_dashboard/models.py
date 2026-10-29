import uuid
from django.contrib.auth.models import User
from django.db import models

from apps.records.models import TrashableModel


class SolarReport(TrashableModel):
    """One client's solar report for a period. Deleting moves it to the trash
    (apps.records): its share link stops working until it's restored."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    client = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='solar_reports',
        null=True,
        blank=True,
    )
    report_date = models.DateField()
    period_start = models.DateField()
    period_end = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    trash_section = 'reports'
    trash_label = 'Solar report'
    audit_exclude = {'uuid'}  # an internal link id, not something people read

    class Meta:
        ordering = ['-created_at']

    FIGURES = ['solar_yield', 'battery_charge', 'usable_solar', 'estimated_saving', 'used_from_battery',
               'sell_to_grid_kwh', 'sell_to_grid_r', 'grid_consumption', 'total_consumption']

    def trash_preview(self, request):
        """The Trash preview: the client, the period and every site's figures with the totals."""
        client, image = None, None
        if self.client_id:
            profile = getattr(self.client, 'client_profile', None)
            image = request.build_absolute_uri(profile.image.url) if profile and profile.image else None
            client = (profile.company_name if profile else '') or self.client.username
        sites = [{'name': r.site.name if r.site_id else '(no site)',
                  'has_battery': bool(r.site_id and r.site.has_battery),
                  **{f: float(getattr(r, f) or 0) for f in self.FIGURES}}
                 for r in self.sites.select_related('site').order_by('order', 'pk')]
        return {
            'kind': 'solar_report',
            'client': client, 'client_image': image,
            'report_date': self.report_date, 'period_start': self.period_start, 'period_end': self.period_end,
            'sites': sites,
            'totals': {f: round(sum(s[f] for s in sites), 2) for f in self.FIGURES},
        }

    def restore_blocked_reason(self):
        # One report per client per period (see SolarReportSerializer.validate).
        if self.client_id and SolarReport.objects.filter(
                client_id=self.client_id, period_start=self.period_start, period_end=self.period_end).exists():
            return (f'this client already has a newer report for {self.period_start:%d/%m/%Y} – '
                    f'{self.period_end:%d/%m/%Y}. Delete that one first if you want this one back.')
        return ''

    def __str__(self):
        if self.client:
            try:
                name = self.client.client_profile.company_name or self.client.username
            except Exception:
                name = self.client.username
        else:
            name = '(no client)'
        return f"{name} — {self.period_start:%d/%m/%Y} to {self.period_end:%d/%m/%Y}"


class Site(models.Model):
    audit_section = 'clients'  # audit history: staff need clients.view to see a site's history
    """A client's property — the one site list shared by solar reports and tickets.

    Holds the site's *identity* (name, address, whether it has a battery). Solar
    reports' per-period numbers live on SiteData, and tickets
    (apps.client_portal.Ticket) point here too. Clients add and rename their own
    sites from the dashboard; APS can also add them while writing a report.
    Retire a site by setting is_active=False: it's hidden from new reports and
    ticket forms but history that already references it keeps working (never
    hard-deleted while history exists).
    """
    client = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='sites',
    )
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)
    has_battery = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    order = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sites_added',
        help_text='Who added it: the client, or blank/staff when APS added it.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['client', 'name'],
                name='uniq_site_per_client',
            ),
        ]

    def __str__(self):
        try:
            client_name = self.client.client_profile.company_name or self.client.username
        except Exception:
            client_name = self.client.username
        return f"{self.name} ({client_name})"


class SiteData(models.Model):
    report = models.ForeignKey(SolarReport, related_name='sites', on_delete=models.CASCADE)
    # Identity of record: every SiteData belongs to a reusable Site. (The legacy
    # site_name/has_battery columns were dropped in migration 0005 — identity now
    # lives on the Site.)
    site = models.ForeignKey(
        Site,
        related_name='data_rows',
        on_delete=models.PROTECT,
    )
    order = models.PositiveSmallIntegerField(default=0)
    solar_yield = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    battery_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    usable_solar = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    estimated_saving = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    used_from_battery = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sell_to_grid_kwh = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sell_to_grid_r = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grid_consumption = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_consumption = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        name = self.site.name if self.site_id else '(no site)'
        return f"{name} ({self.report})"
