"""
Parser for exported WhatsApp chat files.

    >>> ISOLATED ON PURPOSE <<<
This is the ONE module whose behaviour depends on the exact format of the files
the team drops in Google Drive. The rest of the pipeline (Drive auth, listing,
download, dedup, models, task) is format-agnostic and talks to this module
through a single function: `parse_chat_export(text) -> list[ParsedMessage]`.

When a real sample export is available, harden the regex / branches here; nothing
else in the app should need to change.

Current support: the standard WhatsApp "Export chat" .txt format, which looks
like one of these per message (date order and 12h/24h vary by phone locale):

    [2024/03/12, 14:32:05] Thabo: Hi, is the inverter available?
    2024/03/12, 14:32 - Thabo: Hi, is the inverter available?
    3/12/24, 2:32 PM - Thabo: Hi, is the inverter available?

A message can span multiple lines; continuation lines have no timestamp header
and are appended to the previous message. Lines with no sender (system notices
like "Messages and calls are end-to-end encrypted") are treated as system
messages and skipped by default.
"""

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ParsedMessage:
    """One parsed message. `sent_at` is a NAIVE datetime holding the exact
    wall-clock time from the export (no timezone applied here — the caller
    localises it to SAST). `sender` is '' for system messages."""
    sender: str
    sent_at: datetime
    text: str


# Invisible marks WhatsApp sprinkles into exports: LTR/RTL marks, the directional
# isolates that wrap @mentions (U+2066-U+2069, e.g. "@<FSI>Faiez APS<PDI>"),
# embeddings, and BOM. Stripped so senders/text are clean for display.
_INVISIBLE = dict.fromkeys(
    [0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # LRM/RLM/embeddings
     0x2066, 0x2067, 0x2068, 0x2069,                          # directional isolates
     0xFEFF],                                                 # BOM / zero-width no-break
    None,
)

# A message header line, e.g.:
#   [2024/03/12, 14:32:05] Sender: text
#   2024/03/12, 14:32 - Sender: text
# Group 1 = timestamp, group 2 = "Sender: text" (or a system line with no colon).
_HEADER_RE = re.compile(
    r'^\[?'                                   # optional opening bracket
    r'(?P<ts>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4},?\s+'  # date + comma
    r'\d{1,2}:\d{2}(?::\d{2})?'               # time HH:MM(:SS)
    r'(?:\s*[APap][Mm])?)'                    # optional AM/PM
    r'\]?'                                    # optional closing bracket
    r'(?:\s*-\s*|\s+)'                        # separator: " - " or spaces
    r'(?P<rest>.*)$'
)

# Timestamp formats we try, in order. Add locale variants here as needed.
_TS_FORMATS = (
    '%Y/%m/%d, %H:%M:%S',
    '%Y/%m/%d, %H:%M',
    '%d/%m/%Y, %H:%M:%S',
    '%d/%m/%Y, %H:%M',
    '%m/%d/%y, %I:%M %p',
    '%d/%m/%y, %H:%M',
    '%Y-%m-%d, %H:%M:%S',
    '%Y-%m-%d, %H:%M',
    '%d.%m.%Y, %H:%M:%S',
    '%d.%m.%Y, %H:%M',
)


def _parse_timestamp(raw):
    """Return a naive datetime for a raw timestamp string, or None if unparseable."""
    # Normalise exotic spaces (narrow/no-break) to a plain space for strptime.
    raw = raw.strip().replace(' ', ' ').replace(' ', ' ')
    # Normalise AM/PM casing for strptime.
    raw = re.sub(r'\s*([APap][Mm])$', lambda m: ' ' + m.group(1).upper(), raw)
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_chat_export(text, include_system=False):
    """
    Parse the full text of a WhatsApp .txt export into a list of ParsedMessage.

    Multi-line messages are stitched back together. Unparseable header lines and
    (by default) system messages are skipped. This never raises on malformed
    content — it skips what it can't parse so one bad line can't lose a whole
    file. Callers can log the skipped count via the returned stats.
    """
    messages = []
    current = None  # dict being built for the in-progress message

    for raw_line in text.splitlines():
        line = raw_line.translate(_INVISIBLE)

        match = _HEADER_RE.match(line)
        if match:
            ts = _parse_timestamp(match.group('ts'))
            rest = match.group('rest')
            if ts is None:
                # Header-shaped but timestamp not understood — treat as a
                # continuation of the previous message rather than dropping it.
                if current is not None:
                    current['text'] += '\n' + line
                continue

            # Flush the previous message.
            if current is not None:
                messages.append(current)

            # "Sender: text" vs a system line (no "Sender: ").
            sender, sep, body = rest.partition(': ')
            if sep:
                current = {'sender': sender.strip(), 'sent_at': ts, 'text': body}
            else:
                current = {'sender': '', 'sent_at': ts, 'text': rest.strip()}
        else:
            # Continuation line of the current message.
            if current is not None:
                current['text'] += '\n' + line
            # else: leading noise before the first header — ignore.

    if current is not None:
        messages.append(current)

    result = []
    for m in messages:
        if not m['sender'] and not include_system:
            continue
        result.append(ParsedMessage(sender=m['sender'], sent_at=m['sent_at'], text=m['text']))
    return result
