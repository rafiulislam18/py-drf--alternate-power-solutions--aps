from django.db import models


class Client(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255)


class Subscription(models.Model):
    user = models.OneToOneField(Client, on_delete=models.CASCADE)
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True, null=True)
    subscription_length = models.IntegerField(default=0)  # Months
    call_out_balance = models.IntegerField(default=0)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
