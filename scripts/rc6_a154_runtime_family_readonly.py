#!/usr/bin/env python3
"""Bounded, aggregate-only RC6 host observation. No broker or host writes.

The diagnostic Action sends this file to the host on stdin. It never opens a
credential store, starts a service, invokes a provider, or changes SQLite.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo


ROOT = Path('/opt/porota-trading')
DB = ROOT / 'data/paper_v17/observer_v17.db'
ARTIFACTS = DB.parent / 'artifacts' / DB.name
MARKER = ROOT / 'data/deploy/CURRENT_STATE_V2.json'
GUARDIAN = ROOT / 'data/market/iol_oauth_guardian_state.json'
FAMILY_CACHE = ROOT / 'data/market/iol_family_reference_latest.json'
PREOPEN = ROOT / 'data/deploy/paper_scalping_preopen_gate_v1.json'
ART = ZoneInfo('America/Argentina/Buenos_Aires')
SHA = re.compile(r'[0-9a-f]{40}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
CODE = re.compile(r'[A-Z][A-Z0-9_]{0,39}\Z')
UNIT = re.compile(r'porota-[a-z0-9-]+\.timer\Z')
ROLES = ('scanner', 'dynamic_shadow', 'intraday_scalping', 'performance',
         'exit_reader', 'notifications', 'candles', 'caucion_cash_sweep')
FAMILIES = ('ACCIONES', 'CEDEARS', 'ETFS', 'BONOS', 'LETRAS', 'OBLIGACIONES',
            'OPCIONES', 'FUTUROS', 'CAUCIONES', 'FCI')
EXPECTED_DROPLET_ID = '594077619'
EXPECTED_DROPLET_REGION = 'nyc1'


def safe_json(path: Path, maximum: int = 65536) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
        raise ValueError('UNSAFE_JSON_PATH')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('JSON_OBJECT_REQUIRED')
    return data


def optional_json(path: Path, maximum: int = 65536) -> dict:
    try:
        return safe_json(path, maximum)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return {}


def shadow_payload(path: Path, maximum: int = 65536) -> dict:
    """Decode the SHADOW envelope and authenticate its canonical payload."""
    envelope = safe_json(path, maximum)
    if set(envelope) != {'payload', 'digest'} or not isinstance(envelope['payload'], dict):
        raise ValueError('SHADOW_ENVELOPE_INVALID')
    recorded = envelope['digest']
    if not isinstance(recorded, str) or not re.fullmatch(r'[0-9a-f]{64}', recorded):
        raise ValueError('SHADOW_DIGEST_INVALID')
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), default=str,
                               allow_nan=False)
    digest = hashlib.sha256()
    for part in encoder.iterencode(envelope['payload']):
        digest.update(part.encode('ascii'))
    if not hmac.compare_digest(digest.hexdigest(), recorded):
        raise ValueError('SHADOW_DIGEST_MISMATCH')
    return envelope['payload']


def optional_shadow(path: Path, maximum: int = 65536) -> tuple[dict, str]:
    try:
        return shadow_payload(path, maximum), 'VERIFIED'
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        return {}, type(exc).__name__.upper()


def file_metadata(path: Path) -> dict:
    try:
        info = path.lstat()
        return {'present': True, 'regular': stat.S_ISREG(info.st_mode),
                'symlink': stat.S_ISLNK(info.st_mode), 'bytes': info.st_size}
    except OSError:
        return {'present': False}


def iol_cache_probe(path: Path, now: datetime) -> dict:
    """Project only bounded cache metadata; never emit records or quotes."""
    try:
        size = path.lstat().st_size
        payload = safe_json(path, 4 * 1024**2)
        sections = payload.get('section_states')
        return {'read_state': 'VERIFIED', 'bytes': size,
                'cache_state': enum(payload.get('cache_state')),
                'last_known_good_age_s': age(payload.get('last_known_good_at'), now),
                'refresh_age_s': age(payload.get('refreshed_at'), now),
                'section_count': count(len(sections)) if isinstance(sections, dict) else None,
                'continuation_safe': payload.get('continuation_state') ==
                    'CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL'}
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        try:
            size = path.lstat().st_size
        except OSError:
            size = None
        return {'read_state': type(exc).__name__.upper(), 'bytes': size,
                'cache_state': 'NO_VERIFICADO'}


def enum(value) -> str:
    return value if isinstance(value, str) and CODE.fullmatch(value) else 'NO_VERIFICADO'


def family(value) -> str:
    return (value if isinstance(value, str) and
            re.fullmatch(r'[A-Z][A-Z0-9_ /-]{1,39}', value) else 'UNCLASSIFIED_TYPE')


def count(value) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**12 else None


def stamp(value) -> str | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def unit_time(value) -> str | None:
    """Preserve systemd's explicit timezone without guessing a conversion."""
    return (value if isinstance(value, str) and len(value) <= 80 and
            re.fullmatch(r'[A-Za-z0-9 :+./_-]+', value) else None)


def service_result(value) -> str:
    value = value.upper().replace('-', '_') if isinstance(value, str) else ''
    return enum(value)


def journal_classes(service: str) -> dict:
    """Classify two exact failed units without ever publishing their log lines."""
    if service not in {'porota-preopen-rc6.service',
                       'porota-local-eod-history-rc6.service'}:
        raise ValueError('JOURNAL_UNIT_NOT_ALLOWED')
    raw = command(['journalctl', '-u', service, '-n', '20', '--no-pager',
                   '--output=cat'], 2.0)
    if raw is None:
        return {'read': 'UNAVAILABLE', 'classes': []}
    indicators = ('HOST_RESERVE_REQUIRED', 'DEFERRED_DISK',
                  'COLD_ARCHIVE_PHYSICAL_BUDGET', 'CALENDAR_UNVERIFIED',
                  'POSTCLOSE_WINDOW_REQUIRED', 'LOCAL_EOD_SOURCE_READ_BUDGET',
                  'PREOPEN_OPERATOR_PAUSE_DAY_EXPIRED',
                  'PREOPEN_OPERATOR_PAUSE_EVIDENCE_CHANGED',
                  'PREOPEN_DEPLOY_RECEIPT_UNSAFE',
                  'SCHEDULER_A151_OPERATOR_DAY_EXPIRED',
                  'IOL_REFERENCE_UNAVAILABLE', 'BYMA_WATCH_NOT_TODAY',
                  'PAPER_SAFETY_INVARIANT_FAILED')
    classes = [code for code in indicators if code in raw]
    if service == 'porota-preopen-rc6.service' and '"red_checks"' in raw:
        classes.append('PREOPEN_RED_REPORT')
    if service == 'porota-local-eod-history-rc6.service' and '"state": "BLOCKED"' in raw:
        classes.append('LOCAL_EOD_BLOCKED_REPORT')
    return {'read': 'BOUNDED', 'classes': sorted(classes),
            'classification': 'MATCHED' if classes else 'NO_VERIFICADO'}


EXIT2_UNITS = ('porota-preopen-rc6.service',
               'porota-local-eod-history-rc6.service')
PREOPEN_CHECKS = frozenset({
    'release_identity', 'observer_container', 'dashboard_container',
    'observer_db', 'disk', 'required_rc6_timers', 'byma_morning_watch',
    'iol_reference_fallback', 'history_quarantine', 'foreign_market_policy',
    'paper_focus_contracts', 'paper_admission_receipt',
})
EOD_BLOCK_REASONS = frozenset({
    'CALENDAR_UNVERIFIED', 'RUNTIME_CLOSED_UNVERIFIED', 'OPEN_POSITIONS',
    'LOCAL_EOD_HOT_CURSOR_REGRESSION', 'HOST_RESERVE_REQUIRED',
    'COLD_ARCHIVE_PHYSICAL_BUDGET',
})
EXPECTED_A155_SOURCE = '2325eba329878c62a61cb47bb66d33ccf279bd4a'
EXPECTED_A155_DEPLOY = '6676267140907a17e342a664f718cfdcf9b3ce72'


def _systemd_utc(value: str) -> datetime | None:
    try:
        if not value.endswith(' UTC'):
            return None
        return datetime.strptime(value, '%a %Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _failure_reports(messages: list[tuple[datetime, str]], *, unit: str,
                     started: datetime, ended: datetime) -> dict:
    """Decode only the completed failed invocation, never echo journal text."""
    if unit not in EXIT2_UNITS:
        raise ValueError('UNIT_NOT_ALLOWED')
    decoder = json.JSONDecoder()
    reports = []
    bounded = [(at, body[:8192]) for at, body in messages
               if started <= at <= ended and isinstance(body, str)]
    for index, (at, body) in enumerate(bounded):
        if not body.lstrip().startswith('{'):
            continue
        # EOD is one JSON line. Preopen prints a pretty JSON document that
        # journald may split into separate MESSAGE entries.
        candidate = '\n'.join(part for _, part in bounded[index:index + 512])
        try:
            parsed, _ = decoder.raw_decode(candidate.lstrip())
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed.get('schema') in {
                'POROTA_RC6_PREOPEN_V3', 'rc6.local-eod.v1'}:
            reports.append((at, parsed))
    if not reports:
        return {'classification': 'NO_VERIFICADO', 'reason': 'FAILURE_REPORT_UNAVAILABLE'}
    at, report = reports[-1]
    output = {'classification': 'MATCHED', 'report_at_utc': at.isoformat()}
    if unit == EXIT2_UNITS[0]:
        checks = report.get('red_checks')
        if report.get('status') != 'RED' or not isinstance(checks, list) or not checks:
            return {'classification': 'NO_VERIFICADO', 'reason': 'PREOPEN_RED_UNPROVEN'}
        if any(not isinstance(check, str) or check not in PREOPEN_CHECKS
               for check in checks):
            return {'classification': 'NO_VERIFICADO', 'reason': 'PREOPEN_CHECK_UNCLASSIFIED'}
        detail = report.get('checks')
        if not isinstance(detail, dict):
            detail = {}
        output['status'] = 'RED'
        output['red_checks'] = sorted(set(checks))
        output['check_reasons'] = {
            check: enum((detail.get(check) or {}).get('reason'))
            for check in output['red_checks']
            if isinstance(detail.get(check), dict)
        }
        if 'paper_admission_receipt' in checks:
            receipt = report.get('paper_admission_receipt') or {}
            output['paper_admission_reason'] = enum(receipt.get('reason'))
        return output
    state = report.get('state')
    reason = report.get('reason')
    if state not in {'BLOCKED', 'DEFERRED_DISK'} or reason not in EOD_BLOCK_REASONS:
        return {'classification': 'NO_VERIFICADO', 'reason': 'EOD_FAILURE_UNCLASSIFIED'}
    output['state'] = state
    output['reason'] = reason
    return output


def _journal_messages(unit: str, started: datetime, ended: datetime) -> list[tuple[datetime, str]] | None:
    if unit not in EXIT2_UNITS:
        raise ValueError('UNIT_NOT_ALLOWED')
    # Both endpoints are derived from the failed ExecMain invocation, never
    # from a broad day-wide query. No raw lines leave this process.
    since = (started - timedelta(seconds=2)).strftime('%Y-%m-%d %H:%M:%S UTC')
    until = (ended + timedelta(seconds=2)).strftime('%Y-%m-%d %H:%M:%S UTC')
    try:
        result = subprocess.run(
            ['journalctl', '-u', unit, '--since', since, '--until', until,
             '-n', '512', '--no-pager', '--output=json'],
            capture_output=True, timeout=8, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or len(result.stdout) > 512 * 1024:
        return None
    messages = []
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
            at = datetime.fromtimestamp(int(entry['__REALTIME_TIMESTAMP']) / 1_000_000,
                                        timezone.utc)
            body = entry.get('MESSAGE')
            if isinstance(body, str):
                messages.append((at, body))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return messages


def failed_unit_reason(unit: str) -> dict:
    if unit not in EXIT2_UNITS:
        raise ValueError('UNIT_NOT_ALLOWED')
    fields = ('LoadState', 'ActiveState', 'Result', 'ExecMainStatus',
              'ExecMainStartTimestamp', 'ExecMainExitTimestamp')
    raw = command(['systemctl', 'show', unit, '--property=' + ','.join(fields)], 2.0)
    if raw is None:
        return {'classification': 'NO_VERIFICADO', 'reason': 'SYSTEMD_UNAVAILABLE'}
    props = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    started = _systemd_utc(props.get('ExecMainStartTimestamp', ''))
    ended = _systemd_utc(props.get('ExecMainExitTimestamp', ''))
    output = {'active': enum(props.get('ActiveState', '').upper()),
              'result': service_result(props.get('Result')),
              'exit': props.get('ExecMainStatus') if
              re.fullmatch(r'[0-9]{1,3}', str(props.get('ExecMainStatus'))) else None,
              'started_at_utc': started.isoformat() if started else None,
              'ended_at_utc': ended.isoformat() if ended else None}
    if (props.get('LoadState') != 'loaded' or props.get('ActiveState') != 'failed'
            or props.get('Result') != 'exit-code' or props.get('ExecMainStatus') != '2'
            or not started or not ended or not started <= ended
            or (ended - started).total_seconds() > 360):
        return output | {'classification': 'NO_VERIFICADO', 'reason': 'EXIT2_INVOCATION_UNPROVEN'}
    messages = _journal_messages(unit, started, ended)
    if messages is None:
        return output | {'classification': 'NO_VERIFICADO', 'reason': 'BOUNDED_JOURNAL_UNAVAILABLE'}
    return output | _failure_reports(messages, unit=unit, started=started, ended=ended)


def unit_exit2_forensics() -> dict:
    host_id, region = metadata('id'), metadata('region')
    if host_id != EXPECTED_DROPLET_ID or region != EXPECTED_DROPLET_REGION:
        raise ValueError('DROPLET_IDENTITY_MISMATCH')
    marker = safe_json(MARKER)
    if (marker.get('candidate_sha') != EXPECTED_A155_SOURCE or
            marker.get('deploy_sha') != EXPECTED_A155_DEPLOY or
            marker.get('mode') != 'PRODUCTION_PAPER' or
            type(marker.get('real_orders_sent')) is not int or
            marker.get('real_orders_sent') != 0 or
            marker.get('real_order_routes') != 'NOT_CALLED'):
        raise ValueError('A155_PRODUCT_IDENTITY_CHANGED')
    return {'schema': 'rc6.a162.failed-unit-forensics.v1',
            'droplet': {'id': host_id, 'region': region},
            'release': {'candidate_sha': marker['candidate_sha'],
                        'deploy_sha': marker['deploy_sha']},
            'units': {unit: failed_unit_reason(unit) for unit in EXIT2_UNITS},
            'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
            'host_mutations': 0, 'provider_requests': 0,
            'ppi_watch': 'NOT_ACCESSED'}


def age(value, now: datetime) -> float | None:
    parsed = stamp(value)
    if parsed is None:
        return None
    seconds = (now - datetime.fromisoformat(parsed)).total_seconds()
    return round(seconds, 1) if seconds >= 0 else None


def metadata(name: str) -> str:
    if name not in {'id', 'region'}:
        raise ValueError('METADATA_PATH_NOT_ALLOWED')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://169.254.169.254/metadata/v1/' + name, timeout=1.5) as response:
        if response.status != 200:
            raise ValueError('METADATA_NOT_OK')
        value = response.read(64).decode('ascii').strip()
    pattern = r'[0-9]{1,20}' if name == 'id' else r'[a-z0-9-]{2,20}'
    if not re.fullmatch(pattern, value):
        raise ValueError('METADATA_INVALID')
    return value


def command(argv: list[str], timeout: float = 1.5) -> str | None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                check=False)
        return result.stdout[:32768] if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def containers(marker: dict) -> dict:
    result = {}
    expected = marker.get('image_id')
    if not isinstance(expected, str) or not IMAGE.fullmatch(expected):
        raise ValueError('MARKER_IMAGE_INVALID')
    for name in ('porota_production_observer', 'porota_production_dashboard',
                 'porota_critical_approval_rc6'):
        raw = command(['docker', 'inspect', '--format',
                       '{{.Image}}|{{.State.Running}}|{{.RestartCount}}|{{.State.OOMKilled}}',
                       name], 2.0)
        if raw is None:
            raise ValueError('CONTAINER_INSPECT_UNAVAILABLE')
        parts = raw.strip().split('|')
        if len(parts) != 4 or not IMAGE.fullmatch(parts[0]):
            raise ValueError('CONTAINER_IDENTITY_UNVERIFIED')
        result[name] = {'running': parts[1] == 'true',
                        'image_matches_marker': parts[0] == expected,
                        'restarts': int(parts[2]) if re.fullmatch(r'[0-9]{1,8}', parts[2]) else None,
                        'oom': parts[3] == 'true'}
    return result


def connect_ro() -> sqlite3.Connection:
    info = DB.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('PAPER_DB_PATH_UNSAFE')
    conn = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True, timeout=.2)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA busy_timeout=200')
    return conn


def bounded_query(conn: sqlite3.Connection, sql: str, params=(), seconds: float = 1.5):
    deadline = time.monotonic() + seconds
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.set_progress_handler(None, 0)


def table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(bounded_query(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def safety(conn: sqlite3.Connection, marker: dict) -> dict:
    row = bounded_query(conn, 'SELECT mode,real_orders_sent,process_state,session_state,ppi_auth,heartbeat_at '
                              'FROM observer_state WHERE id=1 LIMIT 1')
    if (len(row) != 1 or row[0]['mode'] != 'PRODUCTION_PAPER' or
            type(row[0]['real_orders_sent']) is not int or row[0]['real_orders_sent'] != 0 or
            marker.get('mode') != 'PRODUCTION_PAPER' or
            type(marker.get('real_orders_sent')) is not int or marker['real_orders_sent'] != 0 or
            marker.get('real_order_routes') != 'NOT_CALLED'):
        raise ValueError('PAPER_ZERO_FAIL_CLOSED')
    return {'mode': row[0]['mode'], 'real_orders_sent': 0, 'routes': 'NOT_CALLED',
            'process': enum(row[0]['process_state']), 'session': enum(row[0]['session_state']),
            'ppi_auth': enum(row[0]['ppi_auth']), 'heartbeat_at': stamp(row[0]['heartbeat_at'])}


def pulse() -> dict:
    now = datetime.now(timezone.utc)
    health = optional_json(ARTIFACTS / 'runtime-health.json')
    children = health.get('children') if isinstance(health.get('children'), dict) else {}
    child_status = {name: {'state': enum(children.get(name, {}).get('state')),
                           'restarts': children.get(name, {}).get('restarts')
                           if type(children.get(name, {}).get('restarts')) is int else None,
                           'pid_present': type(children.get(name, {}).get('pid')) is int}
                    for name in ROLES}
    shadow_root = ARTIFACTS / 'dynamic-shadow'
    current, current_read = optional_shadow(shadow_root / 'CURRENT.json', 4096)
    shadow, status_read = optional_shadow(shadow_root / 'status.json', 65536)
    idle, idle_read = optional_shadow(shadow_root / 'idle.json', 4096)
    for payload in (shadow, idle):
        if payload and (type(payload.get('real_orders_sent')) is not int or
                        payload['real_orders_sent'] != 0 or
                        payload.get('real_routes') != 'NOT_CALLED'):
            raise ValueError('SHADOW_PAPER_ZERO_REQUIRED')
    guardian = optional_json(GUARDIAN, 8192)
    cache = iol_cache_probe(FAMILY_CACHE, now)
    gate = optional_json(PREOPEN, 4096)
    with closing(connect_ro()) as conn:
        paper = safety(conn, safe_json(MARKER))
        scalp = {}
        if table(conn, 'intraday_scalping_worker_state'):
            rows = bounded_query(conn, 'SELECT heartbeat_at,state,cursor,selected,successful,failed,'
                                       'candidates,real_orders_sent FROM intraday_scalping_worker_state '
                                       'WHERE id=1 LIMIT 1')
            if rows:
                r = rows[0]
                if count(r['real_orders_sent']) != 0:
                    raise ValueError('SCALPING_REAL_ORDERS_NOT_ZERO')
                scalp = {'state': enum(r['state']), 'heartbeat_at': stamp(r['heartbeat_at']),
                         'heartbeat_age_s': age(r['heartbeat_at'], now),
                         'cursor_numeric': count(r['cursor']), 'selected': count(r['selected']),
                         'successful': count(r['successful']), 'failed': count(r['failed']),
                         'candidates': count(r['candidates']), 'real_orders_sent': 0,
                         'counter_scope': 'CUMULATIVE_NOT_TODAY'}
    generation = current.get('generation_id')
    if not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{64}', generation):
        generation = None
    return {'at_utc': now.isoformat(), 'at_art': now.astimezone(ART).isoformat(),
            'paper': paper, 'paper_heartbeat_age_s': age(paper['heartbeat_at'], now),
            'runtime_health_age_s': age(health.get('recorded_at'), now),
            'runtime_source_sha': health.get('source_sha') if isinstance(health.get('source_sha'), str)
            and SHA.fullmatch(health['source_sha']) else None,
            'children': child_status,
            'shadow': {'generation_id_internal': generation, 'status': enum(shadow.get('status')),
                       'phase': enum(shadow.get('phase')), 'as_of': stamp(shadow.get('as_of')),
                       'current_read': current_read, 'status_read': status_read,
                       'idle_read': idle_read,
                       'files': {name: file_metadata(shadow_root / name)
                                 for name in ('CURRENT.json', 'status.json', 'idle.json')},
                       'idle_heartbeat_age_s': age(idle.get('heartbeat_at'), now),
                       'idle_source_freshness_renewed': idle.get('source_freshness_renewed')
                       if idle_read == 'VERIFIED' and type(idle.get('source_freshness_renewed')) is bool
                       else None},
            'scalping': scalp,
            'iol_guardian': {'state': enum(guardian.get('state')),
                             'checked_age_s': age(guardian.get('checked_at'), now),
                             'authenticated_age_s': age(guardian.get('last_authenticated_at'), now)},
            'iol_cache': cache,
            'preopen': {'status': enum(gate.get('status')),
                        'trade_day_ar': gate.get('trade_day_ar') if re.fullmatch(
                            r'20[0-9]{2}-[0-9]{2}-[0-9]{2}', str(gate.get('trade_day_ar'))) else None}}


def family_counts() -> dict:
    result = {'expected_families': list(FAMILIES)}
    try:
        with closing(connect_ro()) as conn:
            if table(conn, 'catalog_family_coverage'):
                rows = bounded_query(conn, 'SELECT instrument_type,declared,observed_count,ready_paper_count,'
                                           'discovery_status,checked_at FROM catalog_family_coverage '
                                           'ORDER BY instrument_type', seconds=1.5)
                result['coverage'] = [dict(family=enum(r['instrument_type']), declared=count(r['declared']),
                                           observed=count(r['observed_count']), ready=count(r['ready_paper_count']),
                                           discovery=enum(r['discovery_status']), checked_at=stamp(r['checked_at']))
                                      for r in rows if r['instrument_type'] in FAMILIES]
                result['coverage_denominator'] = len(rows)
                result['coverage_extra_types'] = sorted(family(r['instrument_type']) for r in rows
                                                        if r['instrument_type'] not in FAMILIES)
                result['coverage_scope'] = 'PPI_LATEST_QUERY_RESULT_NOT_ACCUMULATED_CATALOG'
            else:
                result['coverage'] = 'TABLE_MISSING'
            if table(conn, 'financial_instrument_catalog') and table(conn, 'candidate_identity_v2'):
                rows = bounded_query(conn, '''SELECT f.instrument_type family, COUNT(*) discovered,
                    SUM(f.status='AVAILABLE') available,
                    SUM(f.status='AVAILABLE' AND f.capability LIKE 'READY_PAPER_%') contract_ready,
                    SUM(f.status='AVAILABLE' AND c.status='AVAILABLE' AND c.can_simulate=1) candidates,
                    MAX(f.last_seen_at) latest_seen
                    FROM financial_instrument_catalog f LEFT JOIN candidate_identity_v2 c
                    ON (f.ticker,f.instrument_type,f.market,f.currency,f.settlement)=
                       (c.ticker,c.instrument_type,c.market,c.currency,c.settlement)
                    GROUP BY f.instrument_type''', seconds=1.5)
                result['catalog'] = [dict(family=enum(r['family']), discovered=count(r['discovered']),
                                          available=count(r['available']), contract_ready=count(r['contract_ready']),
                                          candidates=count(r['candidates']), latest_seen=stamp(r['latest_seen']))
                                     for r in rows if r['family'] in FAMILIES]
                result['catalog_denominator'] = len(rows)
                result['catalog_extra_types'] = sorted(family(r['family']) for r in rows
                                                       if r['family'] not in FAMILIES)
                result['catalog_scope'] = 'ACCUMULATED_PRIMARY_AND_COMPLEMENTARY_NOT_LIVE_READY'
            else:
                result['catalog'] = 'TABLE_MISSING'
            if table(conn, 'ppi_intraday_contract_state'):
                rows = bounded_query(conn, '''SELECT asset_class family,state,COUNT(*) count,
                    MAX(checked_at) latest_checked FROM ppi_intraday_contract_state
                    GROUP BY asset_class,state''', seconds=1.5)
                result['intraday'] = [dict(family=enum(r['family']), state=enum(r['state']),
                                           count=count(r['count']), latest_checked=stamp(r['latest_checked']))
                                      for r in rows if r['family'] in FAMILIES]
                result['intraday_denominator'] = len(rows)
            else:
                result['intraday'] = 'TABLE_MISSING'
    except (sqlite3.Error, OSError, ValueError) as exc:
        result['query_error_class'] = type(exc).__name__
    return result


def timer_states(policy: dict) -> dict:
    units = policy.get('units')
    if not isinstance(units, dict):
        raise ValueError('TIMER_POLICY_INVALID')
    names = sorted(Path(path).name for path, spec in units.items()
                   if isinstance(spec, dict) and spec.get('role') == 'ACTIVE_TIMER'
                   and spec.get('active') is True)
    if len(names) != 16 or len(set(names)) != 16 or any(not UNIT.fullmatch(n) for n in names):
        raise ValueError('TIMER_POLICY_UNEXPECTED')
    if any({'ppi', 'watch'} <= set(name.removesuffix('.timer').split('-')) for name in names):
        raise ValueError('PPI_WATCH_EXCLUDED')
    fields = ('ActiveState', 'UnitFileState', 'Result', 'LastTriggerUSec',
              'NextElapseUSecRealtime', 'Unit')
    result = {}
    deadline = time.monotonic() + 17
    for name in names:
        if time.monotonic() >= deadline:
            result[name] = {'state': 'TIME_BUDGET_EXHAUSTED'}
            continue
        raw = command(['systemctl', 'show', name, '--property=' + ','.join(fields)],
                      min(.75, max(.1, deadline - time.monotonic())))
        if raw is None:
            result[name] = {'state': 'NO_VERIFICADO'}
            continue
        record = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
        service = record.get('Unit')
        if not isinstance(service, str) or not re.fullmatch(r'porota-[a-z0-9-]+\.service', service):
            result[name] = {'state': 'NO_VERIFICADO', 'reason': 'SERVICE_NAME_UNVERIFIED'}
            continue
        child_raw = (command(['systemctl', 'show', service,
                              '--property=ActiveState,Result,ExecMainStatus'],
                             min(.75, max(.1, deadline - time.monotonic())))
                     if time.monotonic() < deadline else None)
        child = dict(line.split('=', 1) for line in child_raw.splitlines() if '=' in line) if child_raw else {}
        result[name] = {'active': enum(record.get('ActiveState', '').upper()),
                        'enabled': enum(record.get('UnitFileState', '').upper()),
                        'last_trigger_host_time': unit_time(record.get('LastTriggerUSec')),
                        'next_elapse_host_time': unit_time(record.get('NextElapseUSecRealtime')),
                        'service_active': enum(child.get('ActiveState', '').upper()),
                        'service_result': service_result(child.get('Result')),
                        'service_exit': child.get('ExecMainStatus') if
                        re.fullmatch(r'[0-9]{1,3}', str(child.get('ExecMainStatus'))) else None}
        if result[name]['service_active'] == 'FAILED' and service in {
                'porota-preopen-rc6.service', 'porota-local-eod-history-rc6.service'}:
            result[name]['failure_classes'] = journal_classes(service)
    return result


def main(encoded_policy: str) -> None:
    begun = time.monotonic()
    policy = json.loads(base64.b64decode(encoded_policy, validate=True))
    host_id, region = metadata('id'), metadata('region')
    if host_id != EXPECTED_DROPLET_ID or region != EXPECTED_DROPLET_REGION:
        raise ValueError('DROPLET_IDENTITY_MISMATCH')
    marker = safe_json(MARKER)
    if not (isinstance(marker.get('candidate_sha'), str) and SHA.fullmatch(marker['candidate_sha'])
            and isinstance(marker.get('deploy_sha'), str) and SHA.fullmatch(marker['deploy_sha'])):
        raise ValueError('MARKER_SHA_INVALID')
    container_state = containers(marker)
    first = pulse()
    # Two short snapshots spanning the configured SHADOW 30s poll, without a
    # prolonged scalping soak. A later independent probe is needed for 180s.
    time.sleep(32)
    second = pulse()
    first_id = first['shadow'].pop('generation_id_internal')
    second_id = second['shadow'].pop('generation_id_internal')
    output = {'schema': 'rc6.a154.runtime-family-ro.v2',
              'droplet': {'id': host_id, 'region': region},
              'release': {'deploy_sha': marker['deploy_sha'], 'candidate_sha': marker['candidate_sha'],
                          'validation': enum(marker.get('validation_status')),
                          'image_id': marker['image_id']},
              'containers': container_state, 'pulses': [first, second],
              'shadow_generation_changed': bool(first_id and second_id and first_id != second_id),
              'families': family_counts(), 'timers': timer_states(policy),
              'elapsed_seconds': round(time.monotonic() - begun, 2),
              'ppi_watch': 'NOT_ACCESSED', 'provider_requests': 0,
              'host_mutations': 0, 'real_orders_sent': 0}
    print('RC6_A154_RUNTIME_FAMILY_RO=' + json.dumps(output, sort_keys=True,
          separators=(',', ':'), allow_nan=False))


if __name__ == '__main__':
    if sys.argv[1:] == ['--unit-forensics-only']:
        print('RC6_A162_UNIT_EXIT2_RO=' + json.dumps(unit_exit2_forensics(),
              sort_keys=True, separators=(',', ':'), allow_nan=False))
        raise SystemExit(0)
    if len(sys.argv) != 2:
        raise SystemExit('POLICY_B64_REQUIRED')
    main(sys.argv[1])
