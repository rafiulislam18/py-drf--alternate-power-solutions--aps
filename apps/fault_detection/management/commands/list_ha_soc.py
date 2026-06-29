"""
Discover Home Assistant SOC sensor entity IDs.

Calls the Home Assistant REST API (/api/states) using HA_TOKEN and prints every
entity whose entity_id or friendly name matches a search term (default "soc"),
together with its current value. Use it to find the 6 IES SOC entity IDs, then
paste them into HA_SOC_ENTITY_IDS in your .env.

Usage:
    python manage.py list_ha_soc
    python manage.py list_ha_soc --match battery   # search a different term
    python manage.py list_ha_soc --all             # list every entity
"""

import re

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "List Home Assistant entities (default: SOC sensors) with current values."

    def add_arguments(self, parser):
        parser.add_argument(
            '--match',
            default='soc',
            help='Case-insensitive substring to match in entity_id / friendly name (default: soc).',
        )
        parser.add_argument(
            '--regex',
            default=None,
            help=(
                'Case-insensitive regex matched against the entity_id only. '
                r'Overrides --match. For the 6 IES SOC sensors use: --regex "sensor\.ies_ies_\d+_soc$"'
            ),
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='List every entity, ignoring --match / --regex.',
        )

    def handle(self, *args, **options):
        if not settings.HA_TOKEN:
            raise CommandError("HA_TOKEN is not set in your .env — cannot authenticate to Home Assistant.")

        base_url = settings.HA_BASE_URL.rstrip('/')
        url = f"{base_url}/api/states"
        headers = {'Authorization': f'Bearer {settings.HA_TOKEN}'}

        try:
            resp = requests.get(url, headers=headers, timeout=settings.HA_REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CommandError(f"Failed to reach Home Assistant at {url}: {exc}")

        states = resp.json()
        match = options['match'].lower()
        show_all = options['all']

        regex = None
        if options['regex']:
            try:
                regex = re.compile(options['regex'], re.IGNORECASE)
            except re.error as exc:
                raise CommandError(f"Invalid --regex pattern: {exc}")

        rows = []
        for entity in states:
            entity_id = entity.get('entity_id', '')
            friendly = (entity.get('attributes') or {}).get('friendly_name', '') or ''
            if show_all:
                keep = True
            elif regex is not None:
                keep = bool(regex.search(entity_id))
            else:
                keep = match in entity_id.lower() or match in friendly.lower()
            if keep:
                rows.append((entity_id, entity.get('state', ''), friendly))

        if not rows:
            self.stdout.write(self.style.WARNING(
                f"No entities matched '{match}'. Try --match with a different term, or --all."
            ))
            return

        rows.sort(key=lambda r: r[0])

        self.stdout.write(self.style.SUCCESS(f"Found {len(rows)} matching entit{'y' if len(rows) == 1 else 'ies'}:\n"))
        width = max(len(r[0]) for r in rows)
        for entity_id, state, friendly in rows:
            label = f"  ({friendly})" if friendly else ""
            self.stdout.write(f"  {entity_id.ljust(width)}  =  {state}{label}")

        # Copy-paste-ready line for the .env — only when the result set is small
        # enough to plausibly be the SOC sensors (a broad --match returns junk).
        if not show_all and len(rows) <= 12:
            ids = ",".join(r[0] for r in rows)
            self.stdout.write("\n" + self.style.HTTP_INFO(
                "If those are your SOC sensors, paste this into .env:"
            ))
            self.stdout.write(f'HA_SOC_ENTITY_IDS = "{ids}"')
        elif not show_all:
            self.stdout.write("\n" + self.style.WARNING(
                f"{len(rows)} matches is too many to be just the SOC sensors. "
                r'Narrow it, e.g.: python manage.py list_ha_soc --regex "sensor\.ies_ies_\d+_soc$"'
            ))
