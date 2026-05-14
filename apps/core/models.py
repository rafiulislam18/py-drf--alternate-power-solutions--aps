from django.contrib.auth.models import User
from django.db import models


class ClientProfile(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('client', 'Client'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='client_profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='client')
    company_name = models.CharField(max_length=200, blank=True)
    image = models.ImageField(upload_to='client_logos/', blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
