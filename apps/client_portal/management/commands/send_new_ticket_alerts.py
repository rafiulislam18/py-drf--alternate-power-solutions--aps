"""Send the pending new-ticket emails now (the same job Celery runs every 30 minutes)."""

from django.core.management.base import BaseCommand

from apps.client_portal.alerts import send_new_ticket_alerts


class Command(BaseCommand):
    help = 'Email the APS team about new tickets, and each client their confirmation.'

    def handle(self, *args, **options):
        result = send_new_ticket_alerts()
        if result['skipped']:
            self.stdout.write(self.style.WARNING('Skipped: another run is in progress.'))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Team told about {result['team_tickets']} ticket(s); "
            f"{result['clients_emailed']} client confirmation(s) sent."
        ))
