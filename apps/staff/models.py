from django.conf import settings
from django.db import models


class StaffAccess(models.Model):
    """The dashboard permissions of one staff (non-superuser) account.

    ``permissions`` is a list of codes from ``apps.staff.catalogue``. A staff
    account without a row gets the default read permissions; superusers never
    need one (they have everything).
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='staff_access')
    permissions = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'staff access'
        verbose_name_plural = 'staff access'

    def __str__(self):
        return f'{self.user.username}: {len(self.permissions)} permission(s)'
