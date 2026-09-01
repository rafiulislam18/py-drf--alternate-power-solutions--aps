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
    inverter_size = models.CharField(max_length=255, blank=True, null=True)
    installed_panels_count = models.CharField(max_length=255, blank=True, null=True)
    address = models.CharField(max_length=510, blank=True, null=True)
    
    # PayFast specific fields
    payfast_token = models.CharField(max_length=255, blank=True, null=True)  # For managing recurring payments
    payfast_payment_id = models.CharField(max_length=255, blank=True, null=True)  # PayFast's payment ID
    
    # Keep these existing fields
    is_active = models.BooleanField(default=False)
    subscription_length = models.IntegerField(default=0)  # Number of months

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_payment_date = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.address}"


class Payment(models.Model):
    """
    An immutable record of a single confirmed PayFast payment.

    One row is written for every COMPLETE ITN we receive, giving a full audit
    trail independent of the (mutable) Subscription counters. This is what the
    self-service portal reads to show and export a subscriber's payment
    history. Deduplicated on ``pf_payment_id`` so PayFast's ITN retries never
    create a double record.
    """

    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        related_name='payments',
        blank=True,
        null=True,
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        related_name='payments',
        blank=True,
        null=True,
    )

    # Amounts exactly as PayFast reports them (strings -> Decimal).
    amount_gross = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    amount_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    amount_net = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # PayFast identifiers.
    pf_payment_id = models.CharField(max_length=255, unique=True)
    m_payment_id = models.CharField(max_length=255, blank=True)
    payfast_token = models.CharField(max_length=255, blank=True, null=True)

    payment_status = models.CharField(max_length=20, blank=True)
    item_name = models.CharField(max_length=255, blank=True)

    # The full ITN payload, kept verbatim so nothing is ever lost.
    raw_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['subscription']),
            models.Index(fields=['pf_payment_id']),
        ]

    def __str__(self):
        return f"{self.client} - R{self.amount_gross} ({self.pf_payment_id})"
