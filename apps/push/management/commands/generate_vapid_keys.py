"""
Make the VAPID key pair for push notifications and print the lines for .env.

    python manage.py generate_vapid_keys --subject mailto:support@example.com

Run it ONCE per server. Changing the keys later means every device has to turn
notifications on again (their sign-ups are tied to the old public key).
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


class Command(BaseCommand):
    help = 'Generate a VAPID key pair for push notifications and print the .env lines.'

    def add_arguments(self, parser):
        parser.add_argument('--subject', default='mailto:you@example.com',
                            help='Contact for the push services, as mailto:address (or an https:// URL).')

    def handle(self, *args, **options):
        subject = options['subject'].strip()
        if not subject.startswith(('mailto:', 'https://')):
            raise CommandError('--subject must start with mailto: (e.g. mailto:support@example.com).')
        key = ec.generate_private_key(ec.SECP256R1())
        private = _b64url(key.private_numbers().private_value.to_bytes(32, 'big'))
        public = _b64url(key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
        ))
        if settings.VAPID_PRIVATE_KEY:
            self.stderr.write(self.style.WARNING(
                'Keys are already set on this server. Replacing them means every device must turn '
                'notifications on again. Only do it if you mean to.'
            ))
        self.stdout.write('Add these three lines to the server\'s .env file (keep the private key secret):\n')
        self.stdout.write(f'VAPID_PUBLIC_KEY={public}')
        self.stdout.write(f'VAPID_PRIVATE_KEY={private}')
        self.stdout.write(f'VAPID_SUBJECT={subject}')
