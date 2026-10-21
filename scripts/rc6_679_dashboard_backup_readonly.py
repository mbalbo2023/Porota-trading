#!/usr/bin/env python3
"""Low-cost authenticated dashboard route census, run inside the existing dashboard container.

No mutation, no account/position values in output, no credential material in logs.
"""
import collections
import html.parser
import json
import os
import re
import time
import urllib.error
import urllib.request

from rc6_trader_dashboard.navigation import DESTINATIONS, view_path
from urllib.parse import urlsplit

ROUTES = tuple(view_path(destination, tab) for destination in DESTINATIONS
               for tab, _ in destination.tabs)
DEADLINE = time.monotonic() + 45
TOKEN = os.environ.get('DASHBOARD_ACCESS_TOKEN', '')
BASE = 'http://127.0.0.1:8000'
SAFE_CODES = re.compile(r'\b(?:SHADOW|GENERATION|COLD|SOURCE|MARKET|PAPER|NO_VERIFICADO|STALE|UNAVAILABLE|NOT_AVAILABLE|COMMITTED|ANNUAL|HISTORY)[A-Z0-9_]{0,72}\b')


class Headings(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.headings = []
        self.links = []
        self.table_count = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('h1', 'h2'):
            self.current = [tag, '']
        elif tag == 'table':
            self.table_count += 1
        elif tag == 'a':
            for key, value in attrs:
                if key == 'href' and value and (value.startswith('/instrumentos/ficha?identity=')
                                                or value.startswith('/analitica/anual?identity=')):
                    self.links.append(value)
    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data
    def handle_endtag(self, tag):
        if self.current is not None and tag == self.current[0]:
            heading = re.sub(r'\s+', ' ', self.current[1]).strip()
            if heading:
                self.headings.append(heading[:100])
            self.current = None


def fetch(path):
    request = urllib.request.Request(BASE + path, headers={'Authorization': 'Bearer ' + TOKEN,
                           'Cache-Control': 'no-store'})
    try:
        with urllib.request.urlopen(request, timeout=2.5) as response:
            return response.status, response.read(1200000).decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        return exc.code, ''
    except (OSError, TimeoutError):
        return 'TIMEOUT', ''


def main():
    if len(TOKEN) < 24:
        raise SystemExit('DASHBOARD_BEARER_NOT_AVAILABLE')
    start = time.monotonic()
    result = {'schema': 'RC6_DASHBOARD_ROUTE_RO_V2', 'routes': [],
              'security': 'BEARER_NOT_LOGGED;HTML_NOT_LOGGED',
              'truncated': False, 'shadow_status': {}}
    code, body = fetch('/api/dashboard/truth')
    if code == 200:
        try:
            truth = json.loads(body)
            result['truth'] = {key: truth.get(key) for key in (
                'mode', 'execution', 'session_state', 'process_state', 'real_orders_sent')}
        except ValueError:
            result['truth'] = {'status': 'INVALID_JSON'}
    else:
        result['truth'] = {'http': code}
    if result['truth'].get('real_orders_sent') != 0 or result['truth'].get('mode') != 'PRODUCTION_PAPER':
        raise SystemExit('PAPER_GUARD_NOT_PROVEN')
    from cg_paper_workspace import database_path
    from rc6_trader_dashboard.projection import Projection, Store
    with Store(database_path()) as store:
        projection = Projection(store)
        cut = projection.shadow
        report = cut.get('report', {})
        quality = report.get('input_quality', {})
        result['shadow_status'] = {
            'state': cut.get('state'), 'reason': cut.get('reason'),
            'fresh': projection.shadow_fresh,
            'liveness': projection.shadow_liveness,
            'as_of': report.get('as_of'),
            'capacity_open_status': report.get('capacity_open_status'),
            'observation_read_truncated': quality.get('observation_read_truncated') if isinstance(quality, dict) else None,
            'engine_count': len(report.get('engines', {})) if isinstance(report.get('engines'), dict) else None,
            'projection_errors': sorted(set(store.errors))}
    exact_identity_link = None
    annual_link = None
    for path in ROUTES:
        if time.monotonic() >= DEADLINE:
            result['truncated'] = True
            break
        began = time.monotonic()
        status, body = fetch(path)
        heading = Headings()
        heading.feed(body)
        codes = sorted(set(SAFE_CODES.findall(re.sub(r'<[^>]*>', ' ', body))))[:30]
        row = {'path': path, 'http': status, 'elapsed_ms': round((time.monotonic()-began)*1000),
               'headings': heading.headings[:8], 'tables': heading.table_count,
               'no_verificado_count': body.upper().count('NO_VERIFICADO'),
               'stale_count': body.upper().count('STALE'), 'codes': codes}
        if path == '/instrumentos':
            row['exact_ficha_links'] = len(heading.links)
            if heading.links:
                exact_identity_link = heading.links[0]
        if path == '/analitica/anual':
            row['annual_identity_links'] = len(heading.links)
            if heading.links:
                annual_link = heading.links[0]
        result['routes'].append(row)
    if annual_link and not result['truncated']:
        status, body = fetch(annual_link)
        result['annual_identity_drilldown'] = {
            'http': status, 'has_report': 'ANNUAL_NO_CANONICAL_BARS' not in body and
                          'ANNUAL_READ_BUDGET_OR_SCHEMA' not in body and
                          'ANNUAL_SOURCE_UNAVAILABLE' not in body,
            'reason_codes': sorted(set(SAFE_CODES.findall(re.sub(r'<[^>]*>', ' ', body))))[:30],
            'no_verificado_count': body.upper().count('NO_VERIFICADO')}
    if exact_identity_link and not result['truncated']:
        from urllib.parse import parse_qs, urlencode
        identity = parse_qs(urlsplit(exact_identity_link).query).get('identity', [''])[0]
        if identity:
            result['exact_identity_drilldown'] = []
            for tab in ('ficha', 'contrato', 'mercado', 'evidencia'):
                path = '/instrumentos/' + tab
                status, body = fetch(path + '?' + urlencode({'identity': identity}))
                heading = Headings()
                heading.feed(body)
                result['exact_identity_drilldown'].append({
                    'tab': tab, 'http': status, 'headings': heading.headings[:8],
                    'tables': heading.table_count,
                    'no_verificado_count': body.upper().count('NO_VERIFICADO'),
                    'stale_count': body.upper().count('STALE'),
                    'codes': sorted(set(SAFE_CODES.findall(re.sub(r'<[^>]*>', ' ', body))))[:30]})
    result['route_count_expected'] = len(ROUTES)
    result['elapsed_seconds'] = round(time.monotonic()-start, 3)
    print('RC6_DASHBOARD_ROUTE_AUDIT=' + json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
