"""
Sync all quote requests (regular + container) to their Google Sheet tabs.

Same logic the nightly task runs. Useful for a manual sync or first backfill.

Usage:
    python manage.py sync_quotes
"""

from django.core.management.base import BaseCommand

from apps.quote_sheet.exporter import QuoteSheetConfigError, sync_quotes


class Command(BaseCommand):
    help = "Push all quote requests (regular + container) to the Google Sheet tabs."

    def handle(self, *args, **options):
        try:
            results = sync_quotes()
        except QuoteSheetConfigError as exc:
            self.stderr.write(self.style.ERROR(f"Sheet not configured: {exc}"))
            return

        for kind, s in results.items():
            if s.get('error'):
                self.stderr.write(self.style.ERROR(
                    f"{kind}: sync error: {s['error']} ({s['sent']} would have been sent)."
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"{kind}: sent {s['sent']} — {s['created']} new, {s['updated']} updated."
                ))
