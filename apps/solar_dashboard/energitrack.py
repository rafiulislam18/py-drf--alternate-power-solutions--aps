"""
Minimal EnergiTrack client for the weekly solar report.

EnergiTrack serves plant data to API clients through its MCP server
(ENERGITRACK_MCP_URL, "Streamable HTTP" transport) with a read-only personal
token — the apiv1 REST API only takes browser sign-in tokens. MCP over HTTP is
plain JSON-RPC: initialize once, then call tools.

We only use get_role_energy_period_summary (configured-role kWh for inclusive
local dates, Africa/Johannesburg) with breakdown=hourly, and fold the hourly
buckets into per-day kWh + coverage.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from django.conf import settings

REPORT_TZ = ZoneInfo('Africa/Johannesburg')
MAX_DAYS_PER_CALL = 31          # EnergiTrack's per-call history window
SECONDS_PER_DAY = 24 * 60 * 60

# Report metric -> (role, bucket field). Grid buckets are signed
# negative=import / positive=export; battery buckets carry charge/discharge.
METRICS = {
    'solar': ('solar', 'positive_energy'),
    'grid_import': ('grid', 'negative_energy_magnitude'),
    'grid_export': ('grid', 'positive_energy'),
    'battery_charge': ('battery', 'charge_kwh'),
    'battery_discharge': ('battery', 'discharge_kwh'),
}


class EnergiTrackError(Exception):
    """EnergiTrack could not be reached, refused the token, or returned an error."""


class EnergiTrackClient:
    def __init__(self, url=None, token=None, timeout=None):
        self.url = url or getattr(settings, 'ENERGITRACK_MCP_URL', 'https://tars.energitrack.io/mcp')
        self.token = token if token is not None else getattr(settings, 'ENERGITRACK_TOKEN', '')
        self.timeout = timeout or getattr(settings, 'ENERGITRACK_TIMEOUT', 60)
        self._session_id = None
        self._id = 0
        self._ready = False

    # -- JSON-RPC over Streamable HTTP ------------------------------------

    def _post(self, message):
        if not self.token:
            raise EnergiTrackError("ENERGITRACK_TOKEN is not set.")
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
        }
        if self._session_id:
            headers['Mcp-Session-Id'] = self._session_id
        try:
            resp = requests.post(self.url, headers=headers, json=message, timeout=self.timeout)
        except requests.RequestException as exc:
            raise EnergiTrackError(f"could not reach EnergiTrack: {exc}") from exc
        if resp.headers.get('mcp-session-id'):
            self._session_id = resp.headers['mcp-session-id']
        if resp.status_code >= 400:
            raise EnergiTrackError(f"EnergiTrack returned HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    def _request(self, method, params):
        self._id += 1
        resp = self._post({'jsonrpc': '2.0', 'id': self._id, 'method': method, 'params': params})
        reply = _parse_reply(resp, self._id)
        if reply is None:
            raise EnergiTrackError(f"no reply to {method}.")
        if reply.get('error'):
            raise EnergiTrackError(f"{method} failed: {reply['error'].get('message') or reply['error']}")
        return reply.get('result') or {}

    def _ensure_ready(self):
        if self._ready:
            return
        self._request('initialize', {
            'protocolVersion': '2025-03-26',
            'capabilities': {},
            'clientInfo': {'name': 'aps-solar-report', 'version': '1.0'},
        })
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self._ready = True

    def call_tool(self, name, arguments):
        """Call one MCP tool and return its structured result (raises EnergiTrackError)."""
        self._ensure_ready()
        result = self._request('tools/call', {'name': name, 'arguments': arguments})
        data = result.get('structuredContent')
        if data is None and result.get('content'):
            text = result['content'][0].get('text', '')
            try:
                data = json.loads(text)
            except ValueError:
                data = text
        if result.get('isError'):
            raise EnergiTrackError(f"{name} failed: {str(data)[:300]}")
        return data

    # -- Report reads -------------------------------------------------------

    def get_plant(self, plant_id):
        return self.call_tool('get_plant', {'plant_id': plant_id})

    def role_hourly(self, plant_id, role, start, end):
        """Hourly buckets for one role over inclusive local dates [start, end]."""
        data = self.call_tool('get_role_energy_period_summary', {
            'plant_id': plant_id,
            'role': role,
            'period': 'date_range',
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'time_zone': 'Africa/Johannesburg',
            'breakdown': 'hourly',
        })
        if not isinstance(data, dict):
            raise EnergiTrackError(f"unexpected {role} reply for plant {plant_id}: {str(data)[:200]}")
        return data.get('buckets') or []

    def daily_metrics(self, plant_id, start, end, metrics):
        """{metric: {date: (kwh, coverage)}} for every local date in [start, end].

        coverage is 0..1 (share of the day EnergiTrack has data for); a day with
        no data at all is (0.0, 0.0). Requests are split into 31-day windows.
        """
        roles = {METRICS[m][0] for m in metrics}
        out = {m: {} for m in metrics}
        for role in sorted(roles):
            kwh = defaultdict(float)
            covered = defaultdict(float)
            chunk_start = start
            while chunk_start <= end:
                chunk_end = min(end, chunk_start + timedelta(days=MAX_DAYS_PER_CALL - 1))
                for bucket in self.role_hourly(plant_id, role, chunk_start, chunk_end):
                    day = datetime.fromisoformat(bucket['start'].replace('Z', '+00:00')).astimezone(REPORT_TZ).date()
                    covered[(day, role)] += bucket.get('covered_seconds') or 0
                    for m in metrics:
                        if METRICS[m][0] == role:
                            kwh[(day, m)] += bucket.get(METRICS[m][1]) or 0
                chunk_start = chunk_end + timedelta(days=1)
            day = start
            while day <= end:
                coverage = min(covered[(day, role)] / SECONDS_PER_DAY, 1.0)
                for m in metrics:
                    if METRICS[m][0] == role:
                        out[m][day] = (kwh[(day, m)], coverage)
                day += timedelta(days=1)
        return out


def _parse_reply(resp, request_id):
    """The JSON-RPC reply for request_id from a JSON or text/event-stream body."""
    if 'text/event-stream' in resp.headers.get('content-type', ''):
        for line in resp.text.splitlines():
            if line.startswith('data:'):
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if msg.get('id') == request_id:
                    return msg
        return None
    try:
        return resp.json()
    except ValueError as exc:
        raise EnergiTrackError(f"EnergiTrack sent a non-JSON reply: {resp.text[:200]}") from exc
