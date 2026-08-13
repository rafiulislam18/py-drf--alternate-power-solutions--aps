from django.db import models


class Subscription(models.Model):
    """
    A Gas Guard monthly subscription for a user.

    Unlike the reference projects (which collect a Client from a checkout form),
    Gas Guard users are already authenticated, so a subscription is tied to the
    User. When PayFast confirms payment (ITN), the linked user's tier is flipped
    to SUBSCRIBED, which unlocks the subscriber features across the app.
    """

    user = models.OneToOneField(
        'gg_users.GasGuardUser',
        on_delete=models.CASCADE,
        related_name='subscription',
    )

    # PayFast fields.
    payfast_token = models.CharField(max_length=255, blank=True, null=True)  # recurring-billing token
    payfast_payment_id = models.CharField(max_length=255, blank=True, null=True)  # PayFast's pf_payment_id

    is_active = models.BooleanField(default=False)
    subscription_length = models.IntegerField(default=0)  # confirmed months paid

    # ── Cylinder-swap add-on (R199/mo) ────────────────────────────────────
    # An optional extra recurring plan on top of the R99 monitoring sub: when a
    # cylinder runs empty, APS swaps it out. Billed as its OWN PayFast recurring
    # subscription (separate token / pf_payment_id), so it can be cancelled
    # independently. Only offered once the monitoring subscription is active.
    swap_addon_active = models.BooleanField(default=False)
    swap_addon_token = models.CharField(max_length=255, blank=True, null=True)
    swap_addon_payment_id = models.CharField(max_length=255, blank=True, null=True)
    swap_addon_length = models.IntegerField(default=0)  # confirmed months paid
    swap_addon_last_payment_date = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_payment_date = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        state = 'active' if self.is_active else 'inactive'
        return f'{self.user} — {state}'


class Payment(models.Model):
    """
    An immutable record of a single confirmed PayFast payment.

    One row is written for every COMPLETE ITN we receive — for either plan —
    giving a full audit trail independent of the (mutable) Subscription
    counters. Deduplicated on ``pf_payment_id`` so PayFast's ITN retries never
    create a double record.
    """

    class Plan(models.TextChoices):
        MONITORING = 'monitoring', 'Monitoring (R99/mo)'
        CYLINDER_SWAP = 'cylinder_swap', 'Cylinder swap add-on (R199/mo)'

    user = models.ForeignKey(
        'gg_users.GasGuardUser',
        on_delete=models.CASCADE,
        related_name='payments',
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        related_name='payments',
        blank=True,
        null=True,
    )

    plan = models.CharField(max_length=20, choices=Plan.choices)

    # Amounts exactly as PayFast reports them (strings → Decimal).
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
            models.Index(fields=['user', 'plan']),
            models.Index(fields=['pf_payment_id']),
        ]

    def __str__(self):
        return f'{self.user} — {self.get_plan_display()} — R{self.amount_gross} ({self.pf_payment_id})'
