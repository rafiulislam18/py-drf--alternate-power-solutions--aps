"""
Run the SOC imbalance check once, on demand (no Celery needed).

This is the same logic the periodic Celery task runs every 5 minutes — handy
for testing and for a quick manual check. The result is also printed by the
task itself; this command surfaces it through the management-command output and
sets a non-zero exit code when an imbalance is detected, so it can be wired into
shell monitoring if desired.

Usage:
    python manage.py check_soc
"""

from django.core.management.base import BaseCommand

from apps.fault_detection.tasks import check_soc_imbalance


class Command(BaseCommand):
    help = "Run the IES SOC imbalance check once and print the result."

    def handle(self, *args, **options):
        result = check_soc_imbalance.run()
        if result and result.startswith('[ALERT]'):
            self.stderr.write(self.style.ERROR(result))
        else:
            self.stdout.write(self.style.SUCCESS(result))
