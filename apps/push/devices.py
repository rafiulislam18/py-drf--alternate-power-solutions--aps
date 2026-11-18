"""A short, human device name from the browser's user agent ("Chrome on Android")."""

import re

_BROWSERS = [
    (r'EdgA?/|EdgiOS/', 'Edge'),
    (r'SamsungBrowser/', 'Samsung Internet'),
    (r'OPR/|OPiOS/', 'Opera'),
    (r'Firefox/|FxiOS/', 'Firefox'),
    (r'CriOS/|Chrome/', 'Chrome'),
    (r'Safari/', 'Safari'),
]
_SYSTEMS = [
    (r'Android', 'Android'),
    (r'iPhone', 'iPhone'),
    (r'iPad', 'iPad'),
    (r'Windows', 'Windows'),
    (r'CrOS', 'ChromeOS'),
    (r'Macintosh|Mac OS X', 'Mac'),
    (r'Linux', 'Linux'),
]


def _first(table, ua):
    return next((name for pattern, name in table if re.search(pattern, ua)), '')


def device_label(user_agent):
    ua = user_agent or ''
    system = _first(_SYSTEMS, ua)
    # An iPhone/iPad Home Screen app doesn't always say "Safari".
    browser = _first(_BROWSERS, ua) or ('Safari' if system in ('iPhone', 'iPad') else '')
    if browser and system:
        return f'{browser} on {system}'
    return browser or system or 'Unknown device'
