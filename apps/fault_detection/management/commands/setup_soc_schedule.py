"""
Register (or update) the 5-minute SOC imbalance check in Celery Beat.

Idempotent: safe to run repeatedly. Creates a 5-minute IntervalSchedule and a
PeriodicTask pointing at the check_soc_imbalance task. Requires a running Celery
worker + beat to actually execute on schedule.

Usage:
    python manage.py setup_soc_schedule
    python manage.py setup_soc_schedule --minutes 10
    python manage.py setup_soc_schedule --disable   # turn the schedule off
"""

from django.core.management.base import BaseCommand
from django_celery_beat.models import IntervalSchedule, PeriodicTask

TASK_PATH = 'apps.fault_detection.tasks.check_soc_imbalance'
TASK_NAME = 'SOC Imbalance Check (every 5 min)'


class Command(BaseCommand):
    help = "Create/update the Celery Beat periodic task for the SOC imbalance check."

    def add_arguments(self, parser):
        parser.add_argument('--minutes', type=int, default=5, help='Interval in minutes (default: 5).')
        parser.add_argument('--disable', action='store_true', help='Disable the periodic task.')

    def handle(self, *args, **options):
        minutes = options['minutes']

        if options['disable']:
            updated = PeriodicTask.objects.filter(task=TASK_PATH).update(enabled=False)
            self.stdout.write(self.style.WARNING(f"Disabled {updated} SOC schedule(s)."))
            return

        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=minutes,
            period=IntervalSchedule.MINUTES,
        )

        task, created = PeriodicTask.objects.update_or_create(
            task=TASK_PATH,
            defaults={
                'name': TASK_NAME,
                'interval': schedule,
                'enabled': True,
            },
        )

        verb = 'Created' if created else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f"{verb} periodic task '{task.name}' -> {TASK_PATH}, every {minutes} min, enabled."
        ))
        self.stdout.write("Ensure a Celery worker AND celery beat are running for it to fire.")
