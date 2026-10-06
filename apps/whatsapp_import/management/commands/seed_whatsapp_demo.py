"""
Add demo WhatsApp messages for the review page (made-up names and numbers).

    python manage.py seed_whatsapp_demo           # add them (skips if already there)
    python manage.py seed_whatsapp_demo --reset   # remove and re-add with fresh dates
    python manage.py seed_whatsapp_demo --clear   # remove them

Demo messages are never pushed to the jobs sheet, even if marked as jobs.
"""

from django.core.management.base import BaseCommand

from apps.whatsapp_import.demo import clear_demo, demo_exists, seed_demo


class Command(BaseCommand):
    help = 'Add (or --reset / --clear) demo WhatsApp messages for the review page.'

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--reset', action='store_true', help='Remove the demo messages and add them again.')
        group.add_argument('--clear', action='store_true', help='Remove the demo messages.')

    def handle(self, *args, **options):
        if options['clear']:
            removed = clear_demo()
            self.stdout.write(self.style.SUCCESS(f'Removed {removed} demo message(s).'))
            return
        if options['reset']:
            clear_demo()
        elif demo_exists():
            self.stdout.write(self.style.WARNING(
                'Demo messages are already there. Use --reset to refresh their dates, or --clear to remove them.'
            ))
            return
        created = seed_demo()
        self.stdout.write(self.style.SUCCESS(
            f'Added {created} demo WhatsApp messages. Remove them any time with --clear.'
        ))
