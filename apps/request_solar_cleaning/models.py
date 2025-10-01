from django.db import models


class Client(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=320)
    phone = models.CharField(max_length=20)
    note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name}"


class Request(models.Model):
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, blank=True, null=True, related_name='subscriptions')
    address = models.CharField(max_length=510, blank=True, null=True)
    
    # PayFast specific fields (replacing Stripe fields)
    paid = models.BooleanField(default=False)
    payfast_token = models.CharField(max_length=255, blank=True, null=True)  # For managing recurring payments
    payfast_payment_id = models.CharField(max_length=255, blank=True, null=True)  # PayFast's payment ID
        
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_payment_date = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.address}"
