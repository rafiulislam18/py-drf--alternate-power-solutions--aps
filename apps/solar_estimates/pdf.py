"""
PDF of an estimate, printed by headless Chromium from the same HTML + CSS as
the on-screen preview (document.render_html), so the two look identical.

The browser runs in a separate short-lived process (pdf_worker.py).
Production (Ubuntu), once:
  sudo venv/bin/playwright install-deps chromium      # system libraries
  venv/bin/playwright install chromium                # AS THE USER gunicorn runs as (browsers go in its
                                                      # ~/.cache/ms-playwright), or set PLAYWRIGHT_BROWSERS_PATH
  sudo apt install fonts-crosextra-carlito            # Calibri-width font: without it the text runs
                                                      # wider and the proposal spills to 6–7 pages
ESTIMATE_PDF_BROWSER_CHANNEL can point at an installed browser instead (e.g.
"msedge" or "chrome" on a dev machine).
"""

import logging
import os
import subprocess
import sys

from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 90


class PdfUnavailable(Exception):
    """The PDF could not be printed (no browser, or it failed)."""


def html_to_pdf(html):
    env = {**os.environ, 'ESTIMATE_PDF_BROWSER_CHANNEL': getattr(settings, 'ESTIMATE_PDF_BROWSER_CHANNEL', '') or ''}
    try:
        done = subprocess.run(
            [sys.executable, '-m', 'apps.solar_estimates.pdf_worker'],
            input=html.encode('utf-8'), capture_output=True, timeout=TIMEOUT_SECONDS,
            cwd=str(settings.BASE_DIR), env=env, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error(f'Estimate PDF failed to run: {exc}')
        raise PdfUnavailable(str(exc)) from exc
    if done.returncode != 0 or not done.stdout.startswith(b'%PDF'):
        reason = done.stderr.decode('utf-8', 'replace').strip().splitlines()[-1:] or ['unknown error']
        logger.error(f'Estimate PDF failed: {reason[0]}')
        raise PdfUnavailable(reason[0])
    return done.stdout
