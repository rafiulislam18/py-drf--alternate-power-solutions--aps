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


class SiteData(models.Model):
    report = models.ForeignKey(SolarReport, related_name='sites', on_delete=models.CASCADE)
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
