"""
Run the low-gas check once, synchronously (no Celery worker needed).

Handy for testing the alert flow, or for driving it from an OS scheduler if you
prefer not to run a Celery worker. The scheduled hourly run uses the same task
via Celery Beat.

    python manage.py run_low_gas_check
"""

from django.core.management.base import BaseCommand

from apps.gas_guard.alerts.tasks import check_low_gas


class Command(BaseCommand):
    help = 'Run the hourly low-gas alert check immediately (synchronously).'

    def handle(self, *args, **options):
        # Call the task body directly so it runs in-process without a broker.
        result = check_low_gas()
        self.stdout.write(self.style.SUCCESS(result))
