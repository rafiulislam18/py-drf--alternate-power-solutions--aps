"""
Diagnostic: POST a tiny probe to the quote sheet web app and print the RAW
response, so you can confirm the live /exec deployment actually has the
handleQuotesSync_ code (and which tabs it touched).

Sends a single throwaway quote with key "probe:0". If the deployment is
up-to-date you'll see a "Quote Requests" tab get one row (key probe:0) that you
can delete. If you get {created:0, updated:0, received:0} the deployment is stale.

Usage:
    python manage.py check_quote_sheet
"""

import json

import requests
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send a probe POST to the quote sheet web app and print the raw response."

    def handle(self, *args, **options):
        url = settings.APS_QUOTE_SHEET_URL
        token = settings.APS_QUOTE_SHEET_TOKEN
        if not url or not token:
            self.stderr.write(self.style.ERROR(
                "APS_QUOTE_SHEET_URL / APS_QUOTE_SHEET_TOKEN not configured."
            ))
            return

        self.stdout.write(f"POSTing probe to: {url}")
        payload = {
            'token': token,
            'type': 'quote_requests',
            'quotes': [{'key': 'probe:0', 'name': 'PROBE - delete me',
                        'email': 'probe@example.com'}],
        }

        try:
            resp = requests.post(url, json=payload, timeout=60,
                                 allow_redirects=True)
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(self.style.ERROR(f"Request failed: {exc}"))
            return

        self.stdout.write(f"HTTP status: {resp.status_code}")
        self.stdout.write(f"Final URL after redirects: {resp.url}")
        self.stdout.write("Raw body (first 800 chars):")
        self.stdout.write(resp.text[:800])

        try:
            data = resp.json()
        except Exception:
            self.stderr.write(self.style.WARNING(
                "\nResponse is NOT JSON. That usually means the /exec URL is "
                "wrong, or the deployment requires Google login (returns an "
                "HTML sign-in page). Redeploy the web app with access = 'Anyone'."
            ))
            return

        self.stdout.write("\nParsed JSON:")
        self.stdout.write(json.dumps(data, indent=2))

        if data.get('received') == 1 and (data.get('created') or data.get('updated')):
            self.stdout.write(self.style.SUCCESS(
                "\nOK — the live deployment HAS the quotes handler. "
                "Delete the 'probe:0' row from the Quote Requests tab, then run "
                "`python manage.py sync_quotes`."
            ))
        else:
            self.stdout.write(self.style.ERROR(
                "\nThe deployment answered but did NOT process the quote "
                "(received/created/updated not as expected). This /exec URL is "
                "running an OLD version without handleQuotesSync_. Redeploy the "
                "deployment whose /exec URL matches this one (New version), or "
                "point APS_QUOTE_SHEET_URL at the correct deployment."
            ))
