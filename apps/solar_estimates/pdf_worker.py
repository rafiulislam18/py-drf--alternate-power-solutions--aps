"""
Print HTML to an A4 PDF with headless Chromium — run as its own process:

    python -m apps.solar_estimates.pdf_worker < estimate.html > estimate.pdf

Called by pdf.html_to_pdf(). Kept out of the web process on purpose: the
browser runs with a fresh event loop of its own (Daphne's loop on Windows
can't start subprocesses), and a browser crash can't take a worker down.
Browser: ESTIMATE_PDF_BROWSER_CHANNEL (e.g. "msedge"), else Playwright's
bundled Chromium, then Edge, then Chrome.
"""

import os
import sys


def main():
    from playwright.sync_api import sync_playwright

    html = sys.stdin.buffer.read().decode('utf-8')
    configured = os.environ.get('ESTIMATE_PDF_BROWSER_CHANNEL', '').strip()
    attempts = [configured] if configured else ['', 'msedge', 'chrome']
    with sync_playwright() as p:
        browser, last = None, None
        for channel in attempts:
            try:
                browser = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
                break
            except Exception as exc:  # noqa: BLE001 — try the next browser
                last = exc
        if browser is None:
            sys.stderr.write(f'No browser could be started: {last}\n')
            return 2
        try:
            page = browser.new_page()
            page.set_content(html, wait_until='load')
            pdf = page.pdf(format='A4', print_background=True, prefer_css_page_size=True,
                           margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'})
        finally:
            browser.close()
    sys.stdout.buffer.write(pdf)
    return 0


if __name__ == '__main__':
    sys.exit(main())
