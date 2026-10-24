"""One bounded read-only integrity-owner and cgroup-cache inventory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

ROOT = Path('/opt/porota-trading')
UNITS = ('porota-full-db-integrity-rc6.timer', 'porota-full-db-integrity-rc6.service',
         'porota-rc6-candle-inmarket-verify.timer', 'porota-rc6-candle-inmarket-verify.service')
PROPERTIES = ('ActiveState', 'SubState', 'UnitFileState', 'LastTriggerUSec', 'Result',
              'ExecMainStatus', 'ExecMainStartTimestamp', 'ExecMainExitTimestamp',
              'MainPID', 'InvocationID', 'FragmentPath', 'NextElapseUSecRealtime')


def command(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=12, check=True).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sha', required=True)
    parser.add_argument('--tree', required=True)
    args = parser.parse_args()
    result = {'schema': 'rc6.native624.integrity-owner-inventory.v1', 'read_only': True,
              'collected_at_epoch': time.time(), 'source_sha': args.sha,
              'source_tree': args.tree, 'ppi_watch_accessed': False,
              'provider_requests': 0, 'service_mutations': 0, 'database_scans': 0,
              'units': [], 'source_files': [], 'containers': []}
    for unit in UNITS:
        values = command(['systemctl', 'show', unit, *['-p' + p for p in PROPERTIES]])
        properties = dict(line.split('=', 1) for line in values.splitlines() if '=' in line)
        item = {'unit': unit, 'properties': properties}
        fragment = Path(properties.get('FragmentPath', ''))
        if fragment.is_file():
            item['fragment_sha256'] = hashlib.sha256(fragment.read_bytes()).hexdigest()
        result['units'].append(item)
    for name in ('scripts/rc6_candle_inmarket_evidence.py', 'rc6_full_db_integrity.py',
                 'rc6_candle_integrity.py'):
        path = ROOT / name
        result['source_files'].append({'path': name, 'bytes': path.stat().st_size,
                                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    rows = json.loads(command(['docker', 'inspect', 'porota_production_observer',
                               'porota_production_dashboard']))
    for row in rows:
        env = dict(v.split('=', 1) for v in row['Config']['Env'] if '=' in v)
        assert env.get('POROTA_BUILD_SHA') == args.sha
        assert env.get('POROTA_CANDIDATE_TREE_SHA') == args.tree
        pid = row['State']['Pid']
        paths = [line.split(':', 2)[2] for line in Path('/proc', str(pid), 'cgroup')
                 .read_text().splitlines() if line.startswith('0::')]
        assert len(paths) == 1
        base = Path('/sys/fs/cgroup').resolve()
        folder = (base / paths[0].lstrip('/')).resolve()
        folder.relative_to(base)
        item = {'id': row['Id'], 'name': row['Name'], 'image': row['Image'],
                'status': row['State']['Status'], 'oom': row['State']['OOMKilled'],
                'restarts': row['RestartCount'], 'cgroup': {}}
        for name in ('memory.current', 'memory.peak', 'memory.stat', 'memory.swap.current',
                     'memory.events', 'cpu.max', 'cpu.weight', 'cpu.stat', 'io.stat'):
            item['cgroup'][name] = (folder / name).read_text().strip()
        result['containers'].append(item)
    with sqlite3.connect((ROOT / 'data/paper_v17/observer_v17.db').as_uri() + '?mode=ro',
                         uri=True, timeout=.1) as conn:
        conn.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 2
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        mode, orders = conn.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        assert mode == 'PRODUCTION_PAPER' and type(orders) is int and orders == 0
    result.update(mode=mode, real_orders_sent=orders, status='OBSERVATION_COMPLETE')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
