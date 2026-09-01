"""
Cross-app subscription adapter for the Manage-Subscriptions portal.

The main APS site collects recurring subscriptions in TWO apps with near-
identical schemas — a checkout ``Client(email)`` owning one or more
``Subscription`` rows:

- ``apps.subscription``            (inverter/backup plans)
- ``apps.request_solar_cleaning``  (solar-cleaning plans)  ← rename pending

This module is the single place those two apps are referenced, so the pending
rename of ``request_solar_cleaning`` is a one-line change here. It normalises
both into a uniform shape the API + frontend render, and mints an opaque,
signed reference for each subscription so the cancel endpoint can target a row
without the frontend ever seeing raw table/PK values — and without being able
to point cancel at a row the caller doesn't own (the owner email is always
re-checked server-side against the token).
"""

from dataclasses import dataclass
from datetime import datetime

from django.core import signing

from apps.subscription.models import Payment as InverterPayment
from apps.subscription.models import Subscription as InverterSubscription
from apps.request_solar_cleaning.models import Payment as SolarPayment
from apps.request_solar_cleaning.models import Subscription as SolarSubscription

# Stable provider keys embedded in the opaque ref. Values are arbitrary but must
# never change once tokens are in the wild.
PROVIDER_INVERTER = 'inverter'
PROVIDER_SOLAR = 'solar'

_PROVIDERS = {
    PROVIDER_INVERTER: {
        'model': InverterSubscription,
        'payment_model': InverterPayment,
        'label': 'Inverter / Backup Plan',
    },
    PROVIDER_SOLAR: {
        'model': SolarSubscription,
        'payment_model': SolarPayment,
        'label': 'Solar Cleaning Plan',
    },
}

# Namespace for signing the opaque ref (tamper-evident, not encrypted).
_REF_SALT = 'subscription_portal.subref'


@dataclass
class PortalSub:
    """One subscription, normalised across both provider apps for the API."""

    ref: str
    display_id: int
    provider: str
    plan_label: str
    address: str
    is_active: bool
    months: int
    last_payment_date: datetime | None
    created_at: datetime
    has_payfast_token: bool

    def as_dict(self):
        def _iso(dt):
            return dt.isoformat() if dt else None

        return {
            'ref': self.ref,
            # Human-readable reference shown in the portal and matching a
            # payment's m_payment_id. NOT accepted by cancel — that still
            # requires the signed `ref`, so exposing this grants no authority.
            'id': self.display_id,
            'provider': self.provider,
            'planLabel': self.plan_label,
            'address': self.address or '',
            'isActive': self.is_active,
            'months': self.months,
            'lastPaymentDate': _iso(self.last_payment_date),
            'createdAt': _iso(self.created_at),
            # Surfaced so the UI can hint when an upstream cancel isn't possible
            # (no recurring token was ever minted); cancel still deactivates.
            'hasPayfastToken': self.has_payfast_token,
        }


def _make_ref(provider, pk):
    """Opaque, signed "provider:pk" the frontend echoes back on cancel."""
    return signing.dumps({'p': provider, 'id': pk}, salt=_REF_SALT)


def parse_ref(ref):
    """
    Decode an opaque ref back to ``(provider, pk)``.

    Raises ``django.core.signing.BadSignature`` on tampering and ``ValueError``
    on an unknown provider, so the caller can answer 400 without leaking why.
    """
    data = signing.loads(ref, salt=_REF_SALT)  # BadSignature on tamper
    provider = data.get('p')
    if provider not in _PROVIDERS:
        raise ValueError('unknown provider')
    return provider, data.get('id')


def _normalise(provider, sub):
    cfg = _PROVIDERS[provider]
    return PortalSub(
        ref=_make_ref(provider, sub.pk),
        display_id=sub.pk,
        provider=provider,
        plan_label=cfg['label'],
        address=getattr(sub, 'address', '') or '',
        is_active=sub.is_active,
        months=sub.subscription_length or 0,
        last_payment_date=sub.last_payment_date,
        created_at=sub.created_at,
        has_payfast_token=bool(sub.payfast_token),
    )


def gather_subscriptions(email):
    """
    Every subscription (active or not) tied to ``email`` across both apps.

    Matches the owning ``Client`` case-insensitively; a person may exist as a
    Client in one app, both, or neither. Returns a list of :class:`PortalSub`,
    newest first.
    """
    out = []
    for provider, cfg in _PROVIDERS.items():
        qs = (
            cfg['model']
            .objects.filter(client__email__iexact=email)
            .select_related('client')
        )
        out.extend(_normalise(provider, sub) for sub in qs)

    out.sort(key=lambda s: s.created_at, reverse=True)
    return out


def get_owned_subscription(email, ref):
    """
    Resolve ``ref`` to its model instance, but ONLY if ``email`` owns it.

    Returns the Subscription instance, or ``None`` if the ref is valid but the
    caller's token email doesn't match the row's Client — the server-side
    ownership re-check that stops a valid token cancelling someone else's plan.
    Raises on a tampered/garbage ref (caller maps to 400).
    """
    provider, pk = parse_ref(ref)  # BadSignature / ValueError bubble up
    model = _PROVIDERS[provider]['model']
    try:
        sub = model.objects.select_related('client').get(pk=pk)
    except model.DoesNotExist:
        return None
    owner = (sub.client.email if sub.client else '') or ''
    if owner.lower() != email.lower():
        return None
    return sub


@dataclass
class PortalPayment:
    """One confirmed payment, normalised across both provider apps."""

    date: datetime
    provider: str
    plan_label: str
    item_name: str
    amount_gross: object
    amount_fee: object
    amount_net: object
    status: str
    pf_payment_id: str
    m_payment_id: str

    def as_dict(self):
        def _dec(value):
            return str(value) if value is not None else None

        return {
            'date': self.date.isoformat() if self.date else None,
            'provider': self.provider,
            'planLabel': self.plan_label,
            'itemName': self.item_name or '',
            'amountGross': _dec(self.amount_gross),
            'amountFee': _dec(self.amount_fee),
            'amountNet': _dec(self.amount_net),
            'currency': 'ZAR',
            'status': self.status or '',
            'pfPaymentId': self.pf_payment_id or '',
            'mPaymentId': self.m_payment_id or '',
        }


def gather_payments(email):
    """
    Every confirmed payment tied to ``email`` across both apps, newest first.

    Sourced from the immutable Payment audit trail rather than the mutable
    Subscription counters, so this is what the portal shows and exports.
    Matches the owning ``Client`` case-insensitively, the same way
    :func:`gather_subscriptions` does.
    """
    out = []
    for provider, cfg in _PROVIDERS.items():
        qs = (
            cfg['payment_model']
            .objects.filter(client__email__iexact=email)
            .select_related('client')
        )
        out.extend(
            PortalPayment(
                date=p.created_at,
                provider=provider,
                plan_label=cfg['label'],
                item_name=p.item_name,
                amount_gross=p.amount_gross,
                amount_fee=p.amount_fee,
                amount_net=p.amount_net,
                status=p.payment_status,
                pf_payment_id=p.pf_payment_id,
                m_payment_id=p.m_payment_id,
            )
            for p in qs
        )

    out.sort(key=lambda p: p.date, reverse=True)
    return out
