#!/usr/bin/env python3
"""Inspect the exact candidate backup-retire allowlist without planning/applying."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types


def main():
    if (len(sys.argv) != 5 or os.geteuid() != 0 or
            not re.fullmatch(r'[0-9a-f]{40}', sys.argv[3]) or
            not re.fullmatch(r'[0-9a-f]{64}', sys.argv[2]) or
            not re.fullmatch(r'[0-9a-f]{40}', sys.argv[4])):
        raise ValueError('BACKUP_SNAPSHOT_IDENTITY_INVALID')
    blob = base64.b64decode(sys.argv[1], validate=True)
    if hashlib.sha256(blob).hexdigest() != sys.argv[2]:
        raise ValueError('BACKUP_SNAPSHOT_SOURCE_CHANGED')
    candidate = sys.argv[3]
    source = types.ModuleType('rc6_679_backup_retire_candidate_readonly')
    exec(compile(blob, 'candidate/scripts/rc6_679_backup_suspension.py', 'exec'),
         source.__dict__)
    # import/exec does not call its guarded main(); only inventory()/digest()
    # are invoked. No plan-out, apply-plan, unlink or file-content hashing.
    rows = source.inventory()
    policy_path = Path('/opt/porota-trading/ops/policy/rc6-disk-housekeeping-v1.json')
    marker_path = Path('/opt/porota-trading/data/deploy/CURRENT_STATE_V2.json')
    for path in (policy_path, marker_path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise ValueError('BACKUP_SNAPSHOT_METADATA_UNSAFE')
    policy = json.loads(policy_path.read_bytes())
    marker = json.loads(marker_path.read_bytes())

    def timer_state(action):
        result = subprocess.run(
            ['systemctl', action, 'porota-host-general-backup-rc6.timer'],
            capture_output=True, text=True, timeout=5)
        if result.returncode not in (0, 1, 3):
            raise ValueError('BACKUP_SNAPSHOT_TIMER_UNAVAILABLE')
        return result.stdout.strip()

    enabled, active = timer_state('is-enabled'), timer_state('is-active')
    files = [{'path': row['path'], 'bytes': row['bytes']} for row in rows]
    report = {
        'schema': 'RC6_733_BACKUP_RETIRE_INVENTORY_READONLY_V1',
        'sampled_at_utc': datetime.now(timezone.utc).isoformat(),
        'candidate_sha': candidate, 'candidate_tree': sys.argv[4],
        'source_sha256': sys.argv[2],
        'file_count': len(rows),
        'logical_bytes': sum(row['bytes'] for row in rows),
        'manifest_sha256': source.digest(rows),
        'files': files,
        'backup_policy_suspended': policy.get('backups', {}).get('suspended'),
        'timer_enabled': enabled, 'timer_active': active,
        'current_marker_mode': marker.get('mode'),
        'current_marker_real_orders_sent': marker.get('real_orders_sent'),
        'side_effects': 'NONE_READ_ONLY',
    }
    report['status'] = ('PASS_INVENTORY' if
                        report['backup_policy_suspended'] is True and
                        enabled == 'masked' and active == 'inactive' and
                        marker.get('mode') == 'PRODUCTION_PAPER' and
                        type(marker.get('real_orders_sent')) is int and
                        marker['real_orders_sent'] == 0
                        else 'NO_GO_INVENTORY')
    print('RC6_733_BACKUP_INVENTORY=' + json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'PASS_INVENTORY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
