"""
Demo subscriptions and payments for one client account, for showing the
dashboard's Subscriptions page (e.g. in a meeting).

    python manage.py seed_subscription_demo                # urban-growth
    python manage.py seed_subscription_demo --username acme
    python manage.py seed_subscription_demo --clear        # remove it all again

Plans are matched to an account by its confirmed email. An account with no
email gets ``accounts@<username>.example`` (an address that can never receive
mail), put back to blank by ``--clear``; one with an unconfirmed email is
refused (a real address is never marked confirmed here). Creates, under that email:

- an active Inverter / Backup plan (R99/month, 7 payments),
- an active Solar Cleaning plan (R199/month, 3 payments),
- a cancelled Inverter / Backup plan (3 payments, earlier this year).

Every row is tagged (checkout Client note ``DEMO_NOTE``, payment ids
``DEMO-…``) so ``--clear`` removes exactly what this made. The plans have no
PayFast token, so the nightly subscriptions sheet never picks them up, and
cancelling one from the dashboard only deactivates it.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import ClientProfile
from apps.request_solar_cleaning import models as solar
from apps.subscription import models as inverter

DEMO_NOTE = 'APS demo data (seed_subscription_demo)'
DEMO_DOMAIN = '.example'


def _months_ago(n, day):
    now = timezone.localtime()
    year, month = now.year, now.month - n
    while month < 1:
        month += 12
        year -= 1
    return now.replace(year=year, month=month, day=day, hour=9, minute=30, second=0, microsecond=0)


def _fee(gross):
    # PayFast reports its fee as a negative amount (roughly 3.5% + R2).
    return -(gross * Decimal('0.035') + Decimal('2.00')).quantize(Decimal('0.01'))


class Command(BaseCommand):
    help = 'Add (or --clear) demo subscriptions and payments for a client account.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='urban-growth')
        parser.add_argument('--clear', action='store_true', help='Remove the demo data instead.')

    @transaction.atomic
    def handle(self, *args, username, clear, **options):
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f'No account "{username}".')
        profile, _ = ClientProfile.objects.get_or_create(user=user, defaults={'role': 'client'})

        if clear:
            self._clear(user, profile)
            return

        if profile.email_verified:
            email = user.email
        elif not user.email:
            # No email at all: give it a demo address that can never receive mail.
            email = f'accounts@{username.replace("-", "")}{DEMO_DOMAIN}'
            user.email = email
            user.save(update_fields=['email'])
            profile.verified_email = email
            profile.save(update_fields=['verified_email'])
        else:
            raise CommandError(
                f'{username} has an unconfirmed email ({user.email}); plans only show for a confirmed one. '
                'Confirm it in the dashboard first (this command never marks a real address as confirmed).'
            )

        self._clear_rows()  # re-running replaces the demo rows
        name = profile.company_name or username
        phone = profile.phone or '021 555 0142'

        inv_client = inverter.Client.objects.create(name=name, email=email, phone=phone, note=DEMO_NOTE)
        sol_client = solar.Client.objects.create(name=name, email=email, phone=phone, note=DEMO_NOTE)

        plans = [
            # (app, client, fields, start months ago, payments, active, amount)
            (inverter, inv_client, {'inverter_type': 'Deye 8 kW hybrid',
                                    'address': 'Block A, 14 Kloof Street, Gardens, Cape Town'}, 6, 7, True, Decimal('99.00')),
            (solar, sol_client, {'inverter_type': 'Sunsynk 5 kW', 'inverter_size': '5 kW', 'installed_panels_count': '24',
                                 'address': 'Block B, 3 Main Road, Wynberg, Cape Town'}, 3, 3, True, Decimal('199.00')),
            (inverter, inv_client, {'inverter_type': 'LuxPower 5 kW',
                                    'address': 'Old office, 22 Station Road, Observatory, Cape Town'}, 9, 3, False, Decimal('99.00')),
        ]
        total = 0
        for n, (app, client, fields, start, count, active, amount) in enumerate(plans, 1):
            day = 6 if n == 1 else 15 if n == 2 else 10
            sub = app.Subscription.objects.create(client=client, is_active=active, subscription_length=count, **fields)
            dates = [_months_ago(start - i, day) for i in range(count)]
            app.Subscription.objects.filter(pk=sub.pk).update(created_at=dates[0], last_payment_date=dates[-1])
            for i, when in enumerate(dates, 1):
                fee = _fee(amount)
                pay = app.Payment.objects.create(
                    client=client, subscription=sub,
                    amount_gross=amount, amount_fee=fee, amount_net=amount + fee,
                    pf_payment_id=f'DEMO-{username}-{n}-{i}', m_payment_id=str(sub.pk),
                    payment_status='COMPLETE', item_name='Monthly Subscription',
                    raw_payload={'demo': True},
                )
                app.Payment.objects.filter(pk=pay.pk).update(created_at=when)
                total += 1

        self.stdout.write(self.style.SUCCESS(
            f'Demo data for {username} ({email}): 3 plans (2 active, 1 cancelled), {total} payments. '
            f'Remove with: python manage.py seed_subscription_demo --username {username} --clear'
        ))

    def _clear_rows(self):
        removed = 0
        for app in (inverter, solar):
            clients = app.Client.objects.filter(note=DEMO_NOTE)
            removed += app.Payment.objects.filter(pf_payment_id__startswith='DEMO-').delete()[0]
            removed += app.Subscription.objects.filter(client__in=clients).delete()[0]
            removed += clients.delete()[0]
        return removed

    def _clear(self, user, profile):
        removed = self._clear_rows()
        if user.email.endswith(DEMO_DOMAIN):
            user.email = ''
            user.save(update_fields=['email'])
            if profile.verified_email.endswith(DEMO_DOMAIN):
                profile.verified_email = ''
                profile.save(update_fields=['verified_email'])
        self.stdout.write(self.style.SUCCESS(f'Removed {removed} demo rows for {user.username}.'))
