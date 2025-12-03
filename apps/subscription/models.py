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


class Subscription(models.Model):
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, blank=True, null=True, related_name='subscriptions')
    inverter_type = models.CharField(max_length=255, blank=True, null=True)
    address = models.CharField(max_length=510, blank=True, null=True)
    
    # PayFast specific fields
    payfast_token = models.CharField(max_length=255, blank=True, null=True)  # For managing recurring payments
    payfast_payment_id = models.CharField(max_length=255, blank=True, null=True)  # PayFast's payment ID
    
    # Keep these existing fields
    is_active = models.BooleanField(default=False)
    subscription_length = models.IntegerField(default=0)  # Number of months
    call_out_balance = models.IntegerField(default=0)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_payment_date = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.address}"
