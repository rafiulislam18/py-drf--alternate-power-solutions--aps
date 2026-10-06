import uuid
from django.contrib.auth.models import User
from django.db import models


class SolarReport(models.Model):
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

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        if self.client:
            try:
                name = self.client.client_profile.company_name or self.client.username
            except Exception:
                name = self.client.username
        else:
            name = '(no client)'
        return f"{name} — {self.period_start} to {self.period_end}"


class Site(models.Model):
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
