"""
Grant or revoke Gas Guard staff access.

Gas Guard keeps its own user table, separate from the APS website's
``auth.User``, so ``createsuperuser`` and the Django admin's staff flag have no
bearing here — a Gas Guard admin has to be promoted explicitly. This is how the
first one gets made.

    python manage.py gg_staff someone@example.com
    python manage.py gg_staff someone@example.com --revoke
    python manage.py gg_staff --list
"""

from django.core.management.base import BaseCommand, CommandError

from apps.gas_guard.users.models import GasGuardUser


class Command(BaseCommand):
    help = 'Grant or revoke Gas Guard admin-panel access (gg_users.is_staff).'

    def add_arguments(self, parser):
        parser.add_argument(
            'email',
            nargs='?',
            help='Email of the Gas Guard account to promote or demote.',
        )
        parser.add_argument(
            '--revoke',
            action='store_true',
            help='Remove staff access instead of granting it.',
        )
        parser.add_argument(
            '--list',
            action='store_true',
            help='List the current Gas Guard staff and exit.',
        )

    def handle(self, *args, **options):
        if options['list']:
            staff = GasGuardUser.objects.filter(is_staff=True).order_by('email')
            if not staff:
                self.stdout.write('No Gas Guard staff yet.')
                return
            self.stdout.write('Gas Guard staff:')
            for user in staff:
                self.stdout.write(f'  {user.email}')
            return

        email = options['email']
        if not email:
            raise CommandError('Provide an email, or use --list.')

        try:
            user = GasGuardUser.objects.get(email__iexact=email.strip())
        except GasGuardUser.DoesNotExist:
            raise CommandError(
                f'No Gas Guard account for {email}. '
                f'(Gas Guard users are separate from the APS website users.)'
            )

        grant = not options['revoke']
        if user.is_staff == grant:
            state = 'already' if grant else 'not'
            self.stdout.write(f'{user.email} is {state} Gas Guard staff — nothing to do.')
            return

        user.is_staff = grant
        user.save(update_fields=['is_staff'])
        verb = 'now' if grant else 'no longer'
        self.stdout.write(
            self.style.SUCCESS(f'{user.email} is {verb} Gas Guard staff.')
        )
