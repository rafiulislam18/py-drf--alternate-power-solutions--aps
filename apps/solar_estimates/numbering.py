"""
Quotation numbers: <prefix>-<YYMM>-<SITE>-<NNN>, e.g. APS-PRJ-2610-CHC-001.

- prefix   — settings (default "APS-PRJ")
- YYMM     — from the quote date
- SITE     — a 3-letter code suggested from the street name, editable on the form
- NNN      — a running counter that starts again each month (or never — a setting)

Numbers are unique (a database constraint backs this up).
"""

import re


def suggest_site_code(address):
    """'22 Chateau Circle, Constantia' → 'CHC' (first two letters of the street's
    first word + first letter of its last word); one word → its first three letters."""
    street = (address or '').split(',')[0]
    words = re.findall(r'[A-Za-z]+', street)
    if not words:
        return ''
    code = words[0][:2] + words[-1][0] if len(words) >= 2 else words[0][:3]
    return code.upper()


def clean_site_code(code):
    return re.sub(r'[^A-Z0-9]', '', (code or '').upper())[:6]


def next_quote_number(prefix, quote_date, site_code, reset, existing):
    """The next free number for ``quote_date`` given the numbers already used."""
    yymm = quote_date.strftime('%y%m')
    site = clean_site_code(site_code) or 'XXX'
    head = re.escape(prefix)
    month = re.escape(yymm) if reset == 'monthly' else r'\d{4}'
    pattern = re.compile(rf'^{head}-{month}-[A-Z0-9]+-(\d+)$')
    used = [int(m.group(1)) for n in existing if (m := pattern.match(n or ''))]
    return f'{prefix}-{yymm}-{site}-{max(used, default=0) + 1:03d}'
