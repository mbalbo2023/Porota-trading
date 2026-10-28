#!/usr/bin/env python3
"""Bounded, aggregate-only RC6 host observation. No broker or host writes.

The diagnostic Action sends this file to the host on stdin. It never opens a
credential store, starts a service, invokes a provider, or changes SQLite.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
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


def enum(value) -> str:
    return value if isinstance(value, str) and CODE.fullmatch(value) else 'NO_VERIFICADO'


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
    current = optional_json(shadow_root / 'CURRENT.json', 4096)
    shadow = optional_json(shadow_root / 'status.json', 32768)
    idle = optional_json(shadow_root / 'idle.json', 4096)
    guardian = optional_json(GUARDIAN, 8192)
    cache = optional_json(FAMILY_CACHE, 65536)
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
                         'cursor_numeric': count(r['cursor']), 'selected': count(r['selected']),
                         'successful': count(r['successful']), 'failed': count(r['failed']),
                         'candidates': count(r['candidates']), 'real_orders_sent': 0}
    generation = current.get('generation_id')
    if not isinstance(generation, str) or len(generation) > 128:
        generation = None
    return {'at_utc': now.isoformat(), 'at_art': now.astimezone(ART).isoformat(),
            'paper': paper, 'paper_heartbeat_age_s': age(paper['heartbeat_at'], now),
            'runtime_health_age_s': age(health.get('recorded_at'), now),
            'runtime_source_sha': health.get('source_sha') if isinstance(health.get('source_sha'), str)
            and SHA.fullmatch(health['source_sha']) else None,
            'children': child_status,
            'shadow': {'generation_id_internal': generation, 'status': enum(shadow.get('status')),
                       'phase': enum(shadow.get('phase')), 'as_of': stamp(shadow.get('as_of')),
                       'idle_heartbeat_age_s': age(idle.get('heartbeat_at'), now),
                       'idle_source_freshness_renewed': idle.get('source_freshness_renewed') is True},
            'scalping': scalp,
            'iol_guardian': {'state': enum(guardian.get('state')),
                             'checked_age_s': age(guardian.get('checked_at'), now),
                             'authenticated_age_s': age(guardian.get('last_authenticated_at'), now)},
            'iol_cache': {'state': enum(cache.get('cache_state')),
                          'source_age_s': age(cache.get('last_known_good_at') or cache.get('refreshed_at'), now)},
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
                        'service_result': enum(child.get('Result', '').upper()),
                        'service_exit': child.get('ExecMainStatus') if
                        re.fullmatch(r'[0-9]{1,3}', str(child.get('ExecMainStatus'))) else None}
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
    output = {'schema': 'rc6.a154.runtime-family-ro.v1',
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
    if len(sys.argv) != 2:
        raise SystemExit('POLICY_B64_REQUIRED')
    main(sys.argv[1])
