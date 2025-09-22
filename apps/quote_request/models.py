from django.db import models
from apps.services_and_projects.models import Service


class QuoteRequest(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='quote_requests')
    message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_quote = models.BooleanField(default=False)

    def __str__(self):
        return f"Quote Request from {self.name} - {self.email}"
