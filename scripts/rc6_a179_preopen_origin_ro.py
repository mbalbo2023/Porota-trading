#!/usr/bin/env python3
"""A179 read-only diagnosis of the exact failed A178 preopen transition.

Stream from an isolated GitHub Action to ``sudo -n python3 -``. It never
modifies the host, retries the deployment, calls a provider, or rearms timers.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

CANDIDATE = '7d62d2f4ed353231da8566a360a1179e99bcc7eb'
DEPLOY = 'd961d19e55d78c68d902a14b41480344365cde1e'
ROOT = Path('/opt/porota-trading')
MAX_BYTES = 65536
QUIESCENCE_SHA256 = '0f016652f884cd27bba0e29e3128da615266d09b8bf7a0a1f9a063d1386f0fc6'


def bounded_json(path: Path) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('UNSAFE_RECEIPT:' + path.name)
    raw = path.read_bytes()
    row = json.loads(raw)
    if type(row) is not dict:
        raise ValueError('RECEIPT_NOT_DICT:' + path.name)
    return row, hashlib.sha256(raw).hexdigest()


def capture(name: str, fn) -> dict:
    try:
        value = fn()
        return {'name': name, 'ok': True, 'value': value}
    except Exception as exc:
        return {'name': name, 'ok': False, 'exception': type(exc).__name__,
                'reason': str(exc)[:200]}


def staged_readonly_probe(root: Path, suspend, rc6_preopen) -> dict:
    """Reproduce the missing Deploy V2 environment, in *this process only*."""
    path = root/'scripts/rc6_deploy_quiescence.py'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 260000:
        raise ValueError('A179_INSTALLED_QUIESCENCE_SOURCE_UNSAFE')
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != QUIESCENCE_SHA256:
        raise ValueError('A179_INSTALLED_QUIESCENCE_SOURCE_SHA_RED')
    encoded = base64.b64encode(gzip.compress(source, mtime=0)).decode('ascii')
    if not 0 < len(encoded) < 150000 or os.environ.get('RC6_A145_QUIESCENCE_GZIP_B64'):
        raise ValueError('A179_STAGED_SOURCE_ENV_UNSAFE')
    os.environ['RC6_A145_QUIESCENCE_GZIP_B64'] = encoded
    try:
        seal = capture('staged_a176_seal', suspend.exact_a176_operator_no_go_continuation)
        timer = capture('staged_preopen_timers',
                        lambda: rc6_preopen.required_timers(CANDIDATE))
    finally:
        os.environ.pop('RC6_A145_QUIESCENCE_GZIP_B64', None)
    return {'source_sha256': QUIESCENCE_SHA256, 'seal': seal,
            'timer_gate': timer, 'host_mutations': 0}


def diagnose(root=ROOT) -> dict:
    marker, marker_sha = bounded_json(root/'data/deploy/CURRENT_STATE_V2.json')
    if (marker.get('candidate_sha') != CANDIDATE or
            marker.get('deploy_sha') != DEPLOY or
            marker.get('validation_status') != 'DEPLOYED_VALIDATION_PENDING' or
            marker.get('mode') != 'PRODUCTION_PAPER' or
            type(marker.get('real_orders_sent')) is not int or
            marker['real_orders_sent'] != 0 or
            marker.get('real_order_routes') != 'NOT_CALLED' or
            marker.get('ppi_watch_untouched') is not True):
        raise ValueError('A179_EXACT_PENDING_PAPER0_MARKER_REQUIRED')
    with sqlite3.connect((root/'data/paper_v17/observer_v17.db').as_uri()+'?mode=ro',
                         uri=True, timeout=3) as db:
        db.execute('PRAGMA query_only=ON')
        book = db.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
    if book != ('PRODUCTION_PAPER', 0):
        raise ValueError('A179_DB_NOT_PAPER0')
    sys.path.insert(0, str(root))
    from scripts import rc6_deploy_scheduler_suspend as suspend
    import rc6_preopen
    policy = (root/'ops/policy/host-control-plane-reconciliation-v2.json').read_bytes()
    names = tuple(suspend.timer_policy(policy))
    guard = suspend.preserved_iol_guard(policy)
    receipt, receipt_sha = bounded_json(
        root/'data/deploy'/('scheduler-suspend-'+CANDIDATE+'.json'))
    quiescence, quiescence_sha = bounded_json(
        root/'data/deploy'/('quiescence-'+CANDIDATE+'.json'))
    frozen, frozen_sha = bounded_json(root/'data/deploy/porota-frozen-candidate.json')
    if (frozen.get('candidate_sha') != CANDIDATE or
            frozen.get('candidate_tree_sha') !=
            '31ced11ce047bfa092f5398c13aeb37b74afd629'):
        raise ValueError('A179_FROZEN_ORIGIN_WRONG_CANDIDATE')
    runtime = quiescence.get('candidate_runtime_after_failure')
    if type(runtime) is not dict:
        runtime = {}
    intent = quiescence.get('candidate_image_intent')
    if type(intent) is not dict:
        intent = {}
    containers = runtime.get('containers')
    if type(containers) is not dict:
        containers = {}
    origin = {
        'quiescence_sha256': quiescence_sha,
        'frozen_sha256': frozen_sha,
        'frozen_image_tar_sha256': frozen.get('image_tar_sha256'),
        'frozen_candidate_tree': frozen.get('candidate_tree_sha'),
        'quiescence_status': quiescence.get('status'),
        'installation_started': quiescence.get('installation_started'),
        'inflight': quiescence.get('inflight'),
        'runtime_health': quiescence.get('runtime_health'),
        'market_open_suspended': quiescence.get('market_open_suspended'),
        'a175_pending_adopted': quiescence.get('a175_pending_adopted'),
        'a175_origin_sha256': quiescence.get('a175_origin_sha256'),
        'image_id': intent.get('image_id'),
        'runtime_status': runtime.get('status'),
        'runtime_flat_book': runtime.get('flat_book'),
        'runtime_containers': {name:{'id':value.get('id'), 'image':value.get('image')}
                               for name,value in containers.items() if type(value) is dict},
        'critical_admission': runtime.get('critical_admission'),
        'real_orders_sent': quiescence.get('real_orders_sent'),
        'ppi_watch_accessed': quiescence.get('ppi_watch_accessed'),
    }
    static = {
        'candidate': receipt.get('candidate_sha') == CANDIDATE,
        'status_suspended': receipt.get('status') == 'SUSPENDED',
        'policy_digest': receipt.get('policy_sha256') == hashlib.sha256(policy).hexdigest(),
        'timer_names': receipt.get('timers') == list(names),
        'attempted_exact': set(receipt.get('attempted', [])) == set(names),
        'guardian': receipt.get('preserved_iol_timer') == guard,
        'ppi_snapshot': receipt.get('ppi_before') == suspend.ppi_snapshot(),
        'preopen_operator_timer': receipt.get('operator_pause_timer') ==
                                  suspend.OPERATOR_PREOPEN_TIMER,
        'historical_preopen_receipt': receipt.get('operator_pause_receipt_sha256') ==
                                      suspend.operator_preopen_hold_evidence(),
        'preopen_disabled': suspend.operator_preopen_timer_disabled(),
    }
    return {
        'schema': 'rc6.a179.preopen-pending-root-cause-readonly.v1',
        'status': 'DIAGNOSED_READ_ONLY',
        'candidate': CANDIDATE,
        'deploy': DEPLOY,
        'marker_sha256': marker_sha,
        'scheduler_sha256': receipt_sha,
        'scheduler_status': str(receipt.get('status'))[:60],
        'origin': origin,
        'timer_count': len(names),
        'static_predicates': static,
        'a176_helper': capture('a176_helper', suspend.a176_quiescence_helper),
        'a176_seal': capture('a176_seal', suspend.exact_a176_operator_no_go_continuation),
        'preopen_timer_gate': capture('preopen_timer_gate',
                                      lambda: rc6_preopen.required_timers(CANDIDATE)),
        'staged_pure_readonly': staged_readonly_probe(root, suspend, rc6_preopen),
        'paper': {'mode': book[0], 'real_orders_sent': book[1],
                  'operational_go': False},
        'host_mutations': 0,
        'ppi_watch_mutations': 0,
    }


if __name__ == '__main__':
    try:
        print('RC6_A179_PREOPEN_RO='+json.dumps(diagnose(), sort_keys=True))
    except Exception as exc:
        print('RC6_A179_PREOPEN_RO_ERROR='+json.dumps({
            'exception': type(exc).__name__, 'reason': str(exc)[:240]}, sort_keys=True))
        raise SystemExit(2)
