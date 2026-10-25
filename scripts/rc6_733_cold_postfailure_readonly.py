#!/usr/bin/env python3
"""Bounded read-only COLD and stop-receipt diagnosis after Deploy V2 failure."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path('/opt/porota-trading')
SHA = '26a95f734cb9eece72e89a1d084c7006a1e05fa2'


def bounded_json(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
        raise ValueError('DIAGNOSTIC_FILE_UNSAFE')
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError('DIAGNOSTIC_JSON_INVALID')
    return value


def main() -> None:
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] != SHA:
        raise ValueError('DIAGNOSTIC_IDENTITY_INVALID')
    sys.path.insert(0, str(ROOT))
    from scripts import rc6_failed_cold_fix_forward as cold

    shadow = ROOT / 'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    deploy = ROOT / 'data/deploy'
    live = cold.enveloped(shadow / 'status.json')
    pointer = cold.enveloped(shadow / 'CURRENT.json')
    generation = pointer['generation_id']
    if not re.fullmatch(r'[0-9a-f]{64}', generation):
        raise ValueError('CURRENT_GENERATION_INVALID')
    cut = cold.enveloped_gzip(shadow / ('cut-' + generation + '.json.gz'))
    snapshot = cut.get('report', {}).get('computed_report', {}).get('snapshot_id')
    q = bounded_json(deploy / ('quiescence-' + SHA + '.json'))
    predecessor = q.get('fix_forward_from')
    predecessor_path = (deploy / ('quiescence-' + predecessor + '.json')
                        if isinstance(predecessor, str) and re.fullmatch(r'[0-9a-f]{40}', predecessor)
                        else None)
    stops = q.get('stop_evidence') or {}
    observer_stop = stops.get('porota_production_observer') or {}
    before, after = observer_stop.get('before') or {}, observer_stop.get('after') or {}
    barrier_path = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
    barrier = bounded_json(barrier_path) if barrier_path.exists() else None
    report = {
        'schema': 'RC6_733_COLD_POSTFAILURE_READONLY_V1',
        'candidate': SHA,
        'live': {key: live.get(key) for key in
                 ('status', 'error_class', 'error_module', 'error_line', 'as_of',
                  'real_orders_sent', 'real_routes')},
        'current_generation': generation,
        'current_snapshot': snapshot,
        'revoked_old_cut_present': (shadow / ('cut-' + cold.CUT + '.json.gz')).exists(),
        'archived_failed_input_present': (deploy / ('rc6-failed-cold-' + cold.IDENT)).is_dir(),
        'quiescence': {key: q.get(key) for key in
                       ('status', 'candidate_sha', 'fix_forward_from', 'installation_started',
                        'inflight', 'paused_postclose', 'intraday_caucion', 'pending_caucion',
                        'market_open_suspended', 'open_paper_carryover', 'spot_carryover',
                        'caucion_carryover', 'real_orders_sent', 'ppi_watch_accessed')},
        'observer_stop': {
            'classification': observer_stop.get('classification'),
            'before_status': before.get('Status'), 'before_started': before.get('StartedAt'),
            'after_status': after.get('Status'), 'after_finished': after.get('FinishedAt'),
            'exit_code': after.get('ExitCode'), 'oom_killed': after.get('OOMKilled'),
        },
        'predicates_after_recovery': {
            'same_stop': cold.proven_stop_cancellation(live, q),
            'prior_stop': cold.proven_prior_stop_cancellation(
                live, q, deploy / ('quiescence-' + cold.PRIOR_CANCELLED_SHA + '.json')),
            'inherited_flat': cold.proven_inherited_flat_stop_cancellation(
                live, q, deploy / 'quiescence-83855c3b08647243dcd706ca7ead929378c4c11d.json'),
            'inherited_pending': cold.proven_inherited_pending_stop_cancellation(live, q, predecessor_path),
            'inherited_closed_flat': cold.proven_inherited_closed_flat_stop_cancellation(live, q, predecessor_path),
            'failed_successor_input_identity': snapshot == cold.CURRENT_FAILED_SUCCESSOR,
        },
        'barrier': barrier,
        'host_mutations': 'NONE_REQUESTED',
    }
    print('RC6_733_COLD_POSTFAILURE=' + json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
