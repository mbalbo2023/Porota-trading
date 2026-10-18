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
    timing = status.get('generation_timing') or {}
    if timing.get('tick_measurement_boundary') == 'BEFORE_STATUS_AND_CURRENT':
        publication = cut.get('publication')
        if (not isinstance(publication,dict)
                or publication.get('generation_id') != pointer['generation_id']
                or publication.get('source_as_of') != status['as_of']
                or type(publication.get('total_seconds')) not in (int,float)
                or not math.isfinite(publication['total_seconds'])
                or publication['total_seconds'] < timing['tick_seconds']
                or publication.get('poll_seconds') != 30):
            raise ValueError('GENERATION_PUBLICATION_COST_UNVERIFIED')
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



_SAFE_FAILURE_REASONS = {
    'GENERATION_PUBLICATION_COST_UNVERIFIED', 'GENERATION_ORDER_REGRESSION',
    'GENERATION_CLOCK_UNVERIFIED', 'GENERATION_STALE_OR_FUTURE',
    'GENERATION_AUTHORITY_UNVERIFIED', 'GENERATION_HASH_UNVERIFIED',
    'GENERATION_SEQUENCE_UNVERIFIED', 'WORKER_SOURCE_IDENTITY_UNVERIFIED',
    'NATIVE_RUNTIME_BUDGET_UNVERIFIED', 'WORKER_HEALTH_UNVERIFIED',
    'COMMITTED_GENERATION_MISSING', 'GENERATION_PATH_INVALID',
    'GENERATION_CUT_MISSING', 'GENERATION_ENVELOPE_MISMATCH',
    'GENERATION_MEMBER_MISMATCH', 'GENERATION_REVOKED_OR_PUBLICATION_IN_PROGRESS',
    'GENERATION_SAFETY_UNVERIFIED', 'SHADOW_EVIDENCE_DIGEST_MISMATCH',
    'SHADOW_PAYLOAD_LIMIT',
    'NATIVE_SOAK_SERVICE_CHANGED', 'NATIVE_SOAK_SERVICE_UNAVAILABLE',
    'NATIVE_SOAK_RAM_RESERVE_BREACHED', 'NATIVE_SOAK_DISK_RESERVE_BREACHED',
}


def _observation_context(cut, at):
    """Reviewed clocks/costs explain RED without exporting worker payloads."""
    if not isinstance(cut, dict) or not isinstance(cut.get('status'), dict):
        return {}
    status = cut['status']
    result = {}
    try:
        stamp = datetime.fromisoformat(status['as_of'].replace('Z', '+00:00'))
        if stamp.tzinfo is not None:
            result['as_of'] = stamp.isoformat()
            age = (at-stamp).total_seconds()
            if math.isfinite(age) and abs(age) <= 86400:
                result['age_seconds'] = round(age, 3)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        pass
    if status.get('status') in {'SHADOW_OBSERVING', 'FAIL_CLOSED',
                                'PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION'}:
        result['worker_status'] = status['status']
    for group, keys in (('generation_timing', ('poll_seconds', 'tick_seconds', 'tick_cpu_seconds')),
                        ('runtime_sql_diagnostics',
                         ('budget_ms', 'sql_elapsed_ms', 'transform_elapsed_ms'))):
        raw = status.get(group)
        if not isinstance(raw, dict):
            continue
        for key in keys:
            value = raw.get(key)
            if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 120000:
                result[key] = value
    timing = status.get('generation_timing')
    allowed = {'state_and_inputs', 'preopen', 'planner_pipeline', 'family_routing',
               'economic_exit_lab', 'entry_signal_lab', 'operational_funnel',
               'durable_serialization'}
    for metric in ('stages_seconds', 'stages_cpu_seconds'):
        phases = timing.get(metric) if isinstance(timing, dict) else None
        if isinstance(phases, dict):
            result[metric] = {key: value for key, value in phases.items()
                if key in allowed and type(value) in (int, float)
                and math.isfinite(value) and 0 <= value <= 120000}
    return result


def collect(root, expected_sha, expected_tree, *, timeout=180, clock=time.monotonic,
            wait=time.sleep, now=lambda: datetime.now(timezone.utc), reader=read_committed_generation,
            minimum_generations=2, strict=False, sample=None):
    if type(minimum_generations) is not int or not 2 <= minimum_generations <= 100 or type(strict) is not bool:
        raise ValueError('GENERATION_COLLECTION_CONTRACT_INVALID')
    start = clock(); first = None; last_error = None; last_reason = None; last_observation = {}
    history = []; rejection_count = 0; generations = []; resources = []
    def remember(kind, detail):
        row = {'kind':kind, 'elapsed_seconds':round(clock()-start,6), **detail}
        # Bound the series without mistaking the last null error for a clean run.
        if len(history) == 128:
            del history[0]
        history.append(row)
    while clock() - start < timeout:
        try:
            if sample is not None:
                resource_receipt = sample()
                if len(resources) == 1024:
                    del resources[0]
                resources.append(resource_receipt)
            health_path = root.parent / 'runtime-health.json'
            if health_path.is_symlink() or health_path.stat().st_size > 131072:
                raise ValueError('WORKER_HEALTH_UNVERIFIED')
            health = json.loads(health_path.read_text())
            if not isinstance(health, dict):
                raise ValueError('WORKER_HEALTH_UNVERIFIED')
            cut = reader(root, payload_limit=4 * 1024**2, require_current_status=True)
            observed_at = now()
            last_observation = _observation_context(cut, observed_at)
            current = receipt(cut, health, expected_sha, expected_tree, observed_at)
            if first and (current['sequence'] < first['sequence'] or
                    (current['sequence'] == first['sequence'] and current['generation_id'] != first['generation_id'])):
                raise ValueError('GENERATION_ORDER_REGRESSION')
            if (strict and first and current['sequence'] != first['sequence']
                    and not advances(first,current)):
                raise ValueError('GENERATION_ORDER_REGRESSION')
            raced = (cut.get('reader_observation') or {}).get('publication_race_retries',0)
            if type(raced) is int and 0 < raced <= 10:
                remember('BOUNDED_PUBLICATION_RACE',{'retries':raced,'sequence':current['sequence']})
            if first and advances(first, current):
                remember('ADVANCING_GENERATION', {'sequence':current['sequence'], **last_observation})
                generations.append(current)
                first = current
                if len(generations) >= minimum_generations:
                    return {'schema': 'rc6.deploy-generation-gate.v1', 'status': 'GREEN',
                            'read_only': True, 'generations': generations,
                            'rejection_count':rejection_count,'observations':history,
                            'minimum_generations':minimum_generations,'strict':strict,'resources':resources}
            if first is None or current['configuration_fingerprint'] != first['configuration_fingerprint']:
                if strict and generations:
                    raise ValueError('GENERATION_ORDER_REGRESSION')
                remember('FIRST_GENERATION', {'sequence':current['sequence'], **last_observation})
                first = current
                generations = [current]
            last_error = None; last_reason = None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Only reviewed literal codes may cross the native log boundary.
            last_error = type(exc).__name__
            last_reason = str(exc) if type(exc) is ValueError and str(exc) in _SAFE_FAILURE_REASONS else None
            # Preserve the exact resource sample that rejected the soak, not
            # merely the last healthy sample. Export only reviewed fields.
            rejected = getattr(exc, 'resource_receipt', None)
            if (last_reason in {'NATIVE_SOAK_SERVICE_CHANGED','NATIVE_SOAK_RAM_RESERVE_BREACHED',
                                'NATIVE_SOAK_DISK_RESERVE_BREACHED'} and isinstance(rejected,dict)
                    and all(type(rejected.get(key)) is int and 0<=rejected[key]<2**63
                            for key in ('available_memory_bytes','free_disk_bytes','swap_used_bytes'))
                    and type(rejected.get('services_unchanged')) is bool
                    and isinstance(rejected.get('at'),str) and len(rejected['at'])<=64):
                if len(resources)==1024:del resources[0]
                resources.append({key:rejected[key] for key in ('at','available_memory_bytes',
                    'free_disk_bytes','swap_used_bytes','services_unchanged')} |
                    {'accepted':False,'rejection_reason':last_reason})
            rejection_count += 1
            remember('REJECTED', {'error_class':last_error,'reason':last_reason, **last_observation})
            if strict:
                return {'schema':'rc6.deploy-generation-gate.v1','status':'RED','read_only':True,
                        'reason':'SUSTAINED_NATIVE_GENERATIONS_REJECTED','error_class':last_error,
                        'error_reason':last_reason,'rejection_count':rejection_count,'observations':history,
                        'generations':generations,'minimum_generations':minimum_generations,
                        'strict':strict,'resources':resources}
            first = None
            generations = []
        wait(2)
    return {'schema': 'rc6.deploy-generation-gate.v1', 'status': 'RED',
            'read_only': True, 'reason': 'TWO_COHERENT_NATIVE_GENERATIONS_NOT_OBSERVED',
            'error_class': last_error, 'error_reason': last_reason,
            'rejection_count':rejection_count,'observations':history,
            'last_observation': last_observation, 'generations': generations,
            'minimum_generations':minimum_generations,'strict':strict,'resources':resources}


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
