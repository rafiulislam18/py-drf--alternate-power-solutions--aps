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
    """A reusable, per-client site (created once, referenced by many reports).

    Holds the site's *identity* (name + whether it has a battery). The changing
    per-period numbers live on SiteData, which points at a Site. Retire a site by
    setting is_active=False: it's hidden from new report forms but old reports that
    already reference it keep working (never hard-deleted while history exists).
    """
    client = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='sites',
    )
    name = models.CharField(max_length=200)
    has_battery = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    order = models.PositiveSmallIntegerField(default=0)
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
    # New identity link. Nullable during the transition (backfilled by data migration
    # 0004); the old site_name/has_battery columns below are kept until the cleanup
    # migration so nothing breaks mid-rollout.
    site = models.ForeignKey(
        Site,
        related_name='data_rows',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    order = models.PositiveSmallIntegerField(default=0)
    site_name = models.CharField(max_length=200)
    has_battery = models.BooleanField(default=False)
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
        return f"{self.site_name} ({self.report})"
