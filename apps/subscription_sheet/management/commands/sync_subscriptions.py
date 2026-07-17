"""
Sync valid subscriptions (both apps) to the APS Subscriptions Google Sheet.

Same logic the nightly task runs. Useful for a manual sync or first backfill.

Usage:
    python manage.py sync_subscriptions
"""

from django.core.management.base import BaseCommand

from apps.subscription_sheet.exporter import (
    SubscriptionSheetConfigError, sync_subscriptions,
)


class Command(BaseCommand):
    help = "Push all valid subscriptions to the APS Subscriptions Google Sheet."

    def handle(self, *args, **options):
        try:
            stats = sync_subscriptions()
        except SubscriptionSheetConfigError as exc:
            self.stderr.write(self.style.ERROR(f"Sheet not configured: {exc}"))
            return

        if stats.get('error'):
            self.stderr.write(self.style.ERROR(
                f"Sync error: {stats['error']} ({stats['sent']} would have been sent)."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Sent {stats['sent']}: {stats['created']} new, {stats['updated']} updated."
        ))
