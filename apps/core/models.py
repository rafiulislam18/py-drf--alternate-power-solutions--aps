from django.contrib.auth.models import User
from django.db import models


class ClientProfile(models.Model):
    """Dashboard account details for a User (one login per client company).

    ``role`` decides what the dashboard shows: 'admin' → the APS staff pages,
    'client' → reports, tickets, sites and subscriptions. See
    :func:`apps.core.roles.get_role` for users without a profile.
    """

    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('client', 'Client'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='client_profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='client')
    company_name = models.CharField(max_length=200, blank=True)
    image = models.ImageField(upload_to='client_logos/', blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True)
    # The address the user proved they own (by clicking an emailed link). The
    # account's email counts as verified only while it still equals this, so an
    # email edited in the admin is unverified again until it's confirmed.
    verified_email = models.EmailField(max_length=254, blank=True)
    # A new address waiting for its confirmation link; user.email only changes
    # once it's confirmed.
    pending_email = models.EmailField(max_length=254, blank=True)
    # Created from the public sign-up page (vs. by APS). These accounts can't
    # sign in at all until their email is verified.
    self_registered = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def email_verified(self):
        email = (self.user.email or '').strip().lower()
        return bool(email) and email == (self.verified_email or '').strip().lower()
