"""Test-only models (installed by config.test_settings) for apps.records."""

from django.conf import settings
from django.db import models

from apps.records.models import TrashableModel


class Widget(TrashableModel):
    STATUSES = [('open', 'Open'), ('done', 'Done')]

    name = models.CharField(max_length=100)
    status = models.CharField(max_length=10, choices=STATUSES, default='open')
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    due = models.DateField(null=True, blank=True)
    paid = models.BooleanField(default=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='+')
    notes_text = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    trash_section = 'reports'
    trash_label = 'Widget'
    audit_exclude = {'notes_text'}

    def __str__(self):
        return self.name


class WidgetNote(models.Model):
    widget = models.ForeignKey(Widget, on_delete=models.CASCADE, related_name='notes')
    text = models.CharField(max_length=100)
