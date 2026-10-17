"""Read-only acceptance of two native SHADOW generations, without running ticks."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_shadow_runtime.persistence import read_committed_generation
from rc6_shadow_runtime.timing import generation_budget
from scripts.porota_image_source_identity import verify_source_identity


def receipt(cut, health, expected_sha, expected_tree, at):
    status, pointer = cut['status'], cut['pointer']
    budget, authority = generation_budget(status)
    stamp = datetime.fromisoformat(status['as_of'].replace('Z', '+00:00'))
    if stamp.tzinfo is None or authority != 'TWO_MEASURED_CYCLES_SOURCE_CAPPED':
        raise ValueError('GENERATION_CLOCK_UNVERIFIED')
    if not 0 <= (at - stamp).total_seconds() <= budget:
        raise ValueError('GENERATION_STALE_OR_FUTURE')
    if (status.get('status') not in {'SHADOW_OBSERVING', 'PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION'}
            or status.get('mode') != 'SHADOW' or type(status.get('provider_requests')) is not int
            or status['provider_requests'] != 0 or type(status.get('real_orders_sent')) is not int
            or status['real_orders_sent'] != 0 or status.get('real_routes') != 'NOT_CALLED'):
        raise ValueError('GENERATION_AUTHORITY_UNVERIFIED')
    for value in (pointer['generation_id'], pointer['manifest_sha256'], status['configuration_fingerprint']):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('GENERATION_HASH_UNVERIFIED')
    if type(pointer.get('sequence')) is not int or pointer['sequence'] <= 0:
        raise ValueError('GENERATION_SEQUENCE_UNVERIFIED')
    if health.get('source_sha') != expected_sha or health.get('candidate_tree_sha') != expected_tree:
        raise ValueError('WORKER_SOURCE_IDENTITY_UNVERIFIED')
    sql = status.get('runtime_sql_diagnostics') or {}
    if not isinstance(sql, dict):
        raise ValueError('NATIVE_RUNTIME_BUDGET_UNVERIFIED')
    def bounded(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 2000
    stages = sql.get('stages')
    if (sql.get('budget_ms') != 2000 or not bounded(sql.get('sql_elapsed_ms'))
            or not bounded(sql.get('transform_elapsed_ms')) or sql.get('transform_expired_stage')
            or not isinstance(stages, list) or not 1 <= len(stages) <= 6
            or any(not isinstance(row, dict) or row.get('deadline_elapsed') is not False
                   or row.get('interrupted_by_deadline') is not False for row in stages)):
        raise ValueError('NATIVE_RUNTIME_BUDGET_UNVERIFIED')
    return {'generation_id': pointer['generation_id'], 'sequence': pointer['sequence'],
            'manifest_sha256': pointer['manifest_sha256'], 'as_of': status['as_of'],
            'configuration_fingerprint': status['configuration_fingerprint'],
            'worker_status': status['status'], 'source_sha': health['source_sha'],
            'candidate_tree_sha': health['candidate_tree_sha'],
            'native_sql_elapsed_ms': sql['sql_elapsed_ms'],
            'native_transform_elapsed_ms': sql['transform_elapsed_ms'],
            'real_orders_sent': 0, 'provider_requests': 0, 'real_routes': 'NOT_CALLED'}


def advances(first, second):
    return (first['configuration_fingerprint'] == second['configuration_fingerprint']
            and first['source_sha'] == second['source_sha']
            and first['candidate_tree_sha'] == second['candidate_tree_sha']
            and second['sequence'] > first['sequence']
            and second['generation_id'] != first['generation_id']
            and datetime.fromisoformat(second['as_of']) > datetime.fromisoformat(first['as_of']))


def collect(root, expected_sha, expected_tree, *, timeout=180, clock=time.monotonic,
            wait=time.sleep, now=lambda: datetime.now(timezone.utc), reader=read_committed_generation):
    start = clock(); first = None; last_error = None
    while clock() - start < timeout:
        try:
            health_path = root.parent / 'runtime-health.json'
            if health_path.is_symlink() or health_path.stat().st_size > 131072:
                raise ValueError('WORKER_HEALTH_UNVERIFIED')
            health = json.loads(health_path.read_text())
            if not isinstance(health, dict):
                raise ValueError('WORKER_HEALTH_UNVERIFIED')
            current = receipt(reader(root, payload_limit=4 * 1024**2, require_current_status=True),
                              health, expected_sha, expected_tree, now())
            if first and advances(first, current):
                return {'schema': 'rc6.deploy-generation-gate.v1', 'status': 'GREEN',
                        'read_only': True, 'generations': [first, current]}
            if first is None or current['configuration_fingerprint'] != first['configuration_fingerprint']:
                first = current
            last_error = None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Error text may contain paths or payloads. Preserve only the class;
            # the canonical worker status carries reviewed causal stage fields.
            last_error = type(exc).__name__
            first = None
        wait(2)
    return {'schema': 'rc6.deploy-generation-gate.v1', 'status': 'RED',
            'read_only': True, 'reason': 'TWO_COHERENT_NATIVE_GENERATIONS_NOT_OBSERVED',
            'error_class': last_error, 'generations': [first] if first else []}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--expected-tree', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    verify_source_identity(args.expected_sha, args.expected_tree)
    db = Path(os.getenv('PAPER_V17_DB_PATH', '/app/data/paper_v17/observer_v17.db'))
    report = collect(db.parent / 'artifacts' / db.name / 'dynamic-shadow', args.expected_sha, args.expected_tree)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp'); temporary.write_text(json.dumps(report, sort_keys=True) + '\n')
    temporary.replace(output)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'GREEN' else 42


if __name__ == '__main__':
    raise SystemExit(main())
