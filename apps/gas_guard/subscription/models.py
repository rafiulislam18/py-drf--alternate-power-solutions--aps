from django.db import models


class Subscription(models.Model):
    """
    A Gas Guard monthly subscription for ONE site (ScaleDevice).

    Billing is per-site: a client subscribes each of their sites separately, so
    a client with three monitored sites has three rows here, each its own R99
    PayFast recurring subscription with its own token. That keeps every site's
    billing independent — adding a site is a fresh checkout, removing one is a
    single cancel, and no existing subscription is ever disturbed.

    The cylinder-swap add-on lives on the same row, so it is likewise per-site
    and can only be bought while THAT site's monitoring is active.

    ``user.tier`` remains account-wide (SUBSCRIBED while any site is active) —
    see ``Subscription.sync_user_tier``.
    """

    user = models.ForeignKey(
        'gg_users.GasGuardUser',
        on_delete=models.CASCADE,
        related_name='subscriptions',
    )
    # The site this subscription pays for. Nullable only so the migration can
    # backfill pre-existing rows; every new row sets it.
    device = models.ForeignKey(
        'gg_weight_scale.ScaleDevice',
        on_delete=models.CASCADE,
        related_name='subscriptions',
        null=True,
        blank=True,
    )

    # PayFast fields.
    payfast_token = models.CharField(max_length=255, blank=True, null=True)  # recurring-billing token
    payfast_payment_id = models.CharField(max_length=255, blank=True, null=True)  # PayFast's pf_payment_id

    is_active = models.BooleanField(default=False)
    subscription_length = models.IntegerField(default=0)  # confirmed months paid

    # ── Cylinder-swap add-on (R199/mo), for THIS site ─────────────────────
    # An optional extra recurring plan on top of this site's R99 monitoring sub:
    # when its cylinder runs empty, APS swaps it out. Billed as its OWN PayFast
    # recurring subscription (separate token / pf_payment_id), so it can be
    # cancelled independently. Only offered while this site's monitoring is
    # active — a client can take the add-on on some sites and not others.
    swap_addon_active = models.BooleanField(default=False)
    swap_addon_token = models.CharField(max_length=255, blank=True, null=True)
    swap_addon_payment_id = models.CharField(max_length=255, blank=True, null=True)
    swap_addon_length = models.IntegerField(default=0)  # confirmed months paid
    swap_addon_last_payment_date = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_payment_date = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['device__name', 'device__device_id']
        constraints = [
            # One subscription row per site. Re-subscribing a cancelled site
            # reuses its row, so m_payment_id stays stable for that site.
            models.UniqueConstraint(
                fields=['device'],
                condition=models.Q(device__isnull=False),
                name='uniq_subscription_per_device',
            ),
        ]

    def __str__(self):
        state = 'active' if self.is_active else 'inactive'
        site = self.device.name if self.device else 'no site'
        return f'{self.user} — {site} — {state}'

    def sync_user_tier(self):
        """
        Keep the account-wide tier in step with the per-site subscriptions.

        ``tier`` gates account-level features (email alerts, and the richer
        dashboard data). With per-site billing there is no single "subscribed"
        answer, so the rule is: SUBSCRIBED while ANY of the user's sites has an
        active monitoring subscription, DEVICE once none do. Per-site data is
        gated on that site's own subscription, not on this flag.
        """
        user = self.user
        any_active = Subscription.objects.filter(user=user, is_active=True).exists()
        wanted = user.Tier.SUBSCRIBED if any_active else user.Tier.DEVICE
        if user.tier != wanted:
            user.tier = wanted
            user.save(update_fields=['tier'])
        return wanted


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
