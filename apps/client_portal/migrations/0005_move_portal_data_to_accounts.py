"""
Fold the invite-only client portal into dashboard accounts — step 2 of 3:
move the data.

Before: a ClientCompany had ClientContacts (email sign-in codes) and its own
ClientSite list; tickets pointed at those. After: one dashboard login per client
(auth.User + core.ClientProfile), one site list per client
(solar_dashboard.Site, shared with the solar reports), and tickets point at those.

Each company is matched to an existing client account — first by a contact's
email, then by company name — or gets a new one (unusable password, email = its
first active contact's, so that person can claim it with "Forgot password").
Each portal site is matched by name to that client's sites or copied across.
The old portal tables are dropped in 0006.

PostgreSQL can't ALTER a table in the same transaction that just inserted
rows referencing it ("pending trigger events"), so this is split into three
migrations, each in its own transaction: 0004 adds the new columns, 0005 moves
the data, 0006 drops the old columns and tables.
"""

from django.contrib.auth.hashers import make_password
from django.db import migrations
from django.utils.text import slugify


def _unique_username(User, base):
    base = (slugify(base)[:30].strip('-_') or 'client')
    candidate, n = base, 1
    while User.objects.filter(username__iexact=candidate).exists() or User.objects.filter(email__iexact=candidate).exists():
        n += 1
        candidate = f'{base}-{n}'
    return candidate


def forwards(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    ClientProfile = apps.get_model('core', 'ClientProfile')
    Site = apps.get_model('solar_dashboard', 'Site')
    ClientCompany = apps.get_model('client_portal', 'ClientCompany')
    ClientContact = apps.get_model('client_portal', 'ClientContact')
    ClientSite = apps.get_model('client_portal', 'ClientSite')
    Ticket = apps.get_model('client_portal', 'Ticket')

    for company in ClientCompany.objects.all():
        contacts = list(ClientContact.objects.filter(company=company).order_by('-is_active', 'id'))
        user = None
        for contact in contacts:
            matches = list(User.objects.filter(email__iexact=contact.email)[:2])
            if len(matches) == 1:
                user = matches[0]
                break
        if user is None:
            profile = ClientProfile.objects.filter(role='client', company_name__iexact=company.name).first()
            user = profile.user if profile else None
        if user is None:
            first = next((c for c in contacts if c.is_active), contacts[0] if contacts else None)
            email = first.email if first and not User.objects.filter(email__iexact=first.email).exists() else ''
            user = User.objects.create(
                username=_unique_username(User, company.name),
                email=email,
                password=make_password(None),
                is_active=company.is_active,
            )
            ClientProfile.objects.create(
                user=user, role='client', company_name=company.name,
                phone=(first.phone if first else '')[:30],
            )

        site_map = {}
        for old in ClientSite.objects.filter(company=company).order_by('id'):
            site = Site.objects.filter(client=user, name__iexact=old.name).first()
            if site is None:
                site = Site.objects.create(
                    client=user, name=old.name, address=old.address,
                    is_active=old.is_active, order=old.order,
                    created_by=user if old.created_by_id else None,
                )
            elif old.address and not site.address:
                site.address = old.address
                site.save(update_fields=['address'])
            site_map[old.pk] = site.pk

        for ticket in Ticket.objects.filter(company=company):
            ticket.client_id = user.pk
            ticket.new_site_id = site_map[ticket.site_id]
            ticket.save(update_fields=['client', 'new_site'])


class Migration(migrations.Migration):

    dependencies = [
        ('client_portal', '0004_ticket_client_new_site'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
