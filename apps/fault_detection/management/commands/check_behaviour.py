"""
Run the charge/discharge behaviour check once, on demand (no Celery needed).

Verifies the battery is charging in Valley and discharging in Peak. Same logic
the periodic Celery task runs every 5 minutes.

Usage:
    python manage.py check_behaviour
"""

from django.core.management.base import BaseCommand

from apps.fault_detection.tasks import check_charge_behaviour


class Command(BaseCommand):
    help = "Run the battery charge/discharge behaviour check once and print the result."

    def handle(self, *args, **options):
        result = check_charge_behaviour.run()
        if result and result.startswith('[ALERT]'):
            self.stderr.write(self.style.ERROR(result))
        else:
            self.stdout.write(self.style.SUCCESS(result))
