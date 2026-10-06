"""Send the ticket-chat unread-message emails now (the same job Celery runs every 30 minutes)."""

from django.core.management.base import BaseCommand

from apps.client_portal.digests import send_chat_digests


class Command(BaseCommand):
    help = 'Email clients and the APS team about ticket chat messages they have not read yet.'

    def handle(self, *args, **options):
        result = send_chat_digests()
        if result['skipped']:
            self.stdout.write(self.style.WARNING('Skipped: another run is in progress.'))
            return
        self.stdout.write(self.style.SUCCESS(
            f"{result['clients_emailed']} client email(s) sent; "
            f"team email {'sent' if result['team_emailed'] else 'not needed'}."
        ))
