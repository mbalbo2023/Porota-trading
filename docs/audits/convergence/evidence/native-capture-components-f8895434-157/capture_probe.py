"""Diagnostic timing only: original private-capture APIs, fixed .25 s budget."""
from pathlib import Path
import ast
import gc
import hashlib
import importlib.metadata
import json
import os
import re
import resource
import socket
import sys
import time
from urllib.parse import unquote, urlsplit

SOURCE = Path('/tmp/rc6-v3-big-f8895434-157-source')
RAW = Path('/tmp/rc6-source-capture-f8895434-157-diagnostic-raw')
ORIGINAL_RAW = Path('/tmp/rc6-v3-big-f8895434-157-raw')
DATABASE = Path('/workspace/rc6-native-big-f8895434-157/data/paper_v17/observer_v17.db')
INTERPRETER = '/workspace/venv_rc6_frozen311/bin/python'
os.umask(0o077)
assert sys.executable == INTERPRETER
original_entry = json.loads((ORIGINAL_RAW / 'entry-preflight.json').read_bytes())
installed = sorted([{'name': re.sub(r'[-_.]+', '-', d.metadata['Name']).lower(), 'version': d.version}
                    for d in importlib.metadata.distributions()], key=lambda row: row['name'])
assert len(installed) == 157 and installed == original_entry['distributions'] and original_entry['passed']
for name, entry in original_entry['locks'].items():
    assert hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() == entry['sha256']
# Load only the original external wrapper's stdlib definitions; never its
# top-level benchmark/preflight/fixture calls. The source inventory is identical.
wrapper = (ORIGINAL_RAW / 'run-wrapper.py').read_text()
namespace = {}
exec(compile(wrapper.split('\nchecks = preflight()\n', 1)[0], '<original-wrapper-definitions>', 'exec'), namespace)
code_before = namespace['inventory']()
network_attempts = []
def forbidden_network(*args, **kwargs):
    network_attempts.append('NETWORK_ATTEMPT')
    raise RuntimeError('OFFLINE_DIAGNOSTIC_NETWORK_FORBIDDEN')
socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = forbidden_network
socket.create_connection = socket.getaddrinfo = forbidden_network
sys.path.insert(0, str(SOURCE))
from scripts.rc6_issue465_stress import PRE, source_custody_snapshot
from scripts.rc6_sqlite_scratch_guard import runtime_settings
from cg_paper_workspace import artifact_root
from rc6_audit_evidence import sqlite_snapshot as snapshot
from rc6_audit_evidence import sqlite_scratch as scratch
from rc6_shadow_runtime.source_reads import source_connection
from rc6_shadow_runtime.families import family_reports
from rc6_dynamic_universe.runtime import read_runtime

scratch_root = artifact_root(DATABASE) / 'sqlite-read-scratch'
settings = runtime_settings(scratch_root)
os.environ.update({'DATA_DIR': str(DATABASE.parent.parent), 'PAPER_V17_DB_PATH': str(DATABASE),
                   'POROTA_DYNAMIC_CAPACITY_MODE': 'OFF', **settings})
source_before = source_custody_snapshot(DATABASE)
active = None
current_pass = None
gc_scope = 'SETUP'
gc_started = {}
gc_events = []
connection_targets = []

def gc_callback(phase, info):
    generation = info['generation']
    if phase == 'start':
        gc_started[generation] = (time.monotonic(), time.process_time(), gc_scope)
    elif generation in gc_started:
        wall, cpu, scope = gc_started.pop(generation)
        gc_events.append({'scope': scope, 'generation': generation,
                          'started_at_monotonic': wall,
                          'wall_seconds': time.monotonic() - wall,
                          'cpu_seconds': time.process_time() - cpu,
                          'collected': info['collected'], 'uncollectable': info['uncollectable']})

def metric(name, wall, cpu, *, count=1, size=0):
    if active is None:
        return
    value = active['operations'].setdefault(name, {'calls': 0, 'bytes': 0, 'wall_seconds': 0., 'cpu_seconds': 0.})
    value['calls'] += count
    value['bytes'] += size
    value['wall_seconds'] += time.monotonic() - wall
    value['cpu_seconds'] += time.process_time() - cpu

class StreamProxy:
    def __init__(self, original):
        self.original = original
    def __enter__(self):
        self.original.__enter__()
        return self
    def __exit__(self, *arguments):
        return self.original.__exit__(*arguments)
    def __getattr__(self, name):
        return getattr(self.original, name)
    def read(self, *arguments):
        wall, cpu = time.monotonic(), time.process_time()
        value = self.original.read(*arguments)
        metric((current_pass or 'unscoped') + ':source_stream_read', wall, cpu, size=len(value))
        return value

class OsProxy:
    def __getattr__(self, name):
        return getattr(os, name)
    def fdopen(self, *arguments, **keywords):
        original = os.fdopen(*arguments, **keywords)
        return StreamProxy(original) if active is not None and current_pass is not None else original

class HashProxy:
    def __init__(self, original):
        self.original = original
    def __getattr__(self, name):
        return getattr(self.original, name)
    def update(self, value):
        wall, cpu = time.monotonic(), time.process_time()
        result = self.original.update(value)
        metric((current_pass or 'unscoped') + ':sha256_update', wall, cpu, size=len(value))
        return result

class HashModuleProxy:
    def __getattr__(self, name):
        return getattr(hashlib, name)
    def sha256(self, *arguments, **keywords):
        original = hashlib.sha256(*arguments, **keywords)
        return HashProxy(original) if active is not None else original

class DestinationProxy:
    def __init__(self, original):
        self.original = original
    def write(self, value):
        wall, cpu = time.monotonic(), time.process_time()
        result = self.original.write(value)
        metric((current_pass or 'unscoped') + ':destination_write', wall, cpu, size=len(value))
        return result

actual_sqlite = snapshot.sqlite3
class SqliteProxy:
    def __getattr__(self, name):
        return getattr(actual_sqlite, name)
    def connect(self, target, *arguments, **keywords):
        path = Path(unquote(urlsplit(str(target)).path)) if str(target).startswith('file:') else Path(target)
        connection_targets.append(str(path))
        if path.absolute() == DATABASE or scratch_root not in path.absolute().parents:
            raise RuntimeError('SOURCE_SQLITE_OR_NONCANONICAL_SCRATCH_FORBIDDEN')
        return actual_sqlite.connect(target, *arguments, **keywords)

actual_read = snapshot._read
def timed_read(member, expected, *, deadline, destination=None, scratch_guard=None):
    global current_pass
    if active is None:
        return actual_read(member, expected, deadline=deadline, destination=destination, scratch_guard=scratch_guard)
    key = str(member)
    index = active['read_passes'].get(key, 0) + 1
    active['read_passes'][key] = index
    prior = current_pass
    current_pass = ('FIRST_COPY' if destination is not None else 'REREAD_SHA' if index > 1 else 'FIRST_SHA_ONLY')
    wall, cpu = time.monotonic(), time.process_time()
    try:
        value = actual_read(member, expected, deadline=deadline,
                            destination=DestinationProxy(destination) if destination is not None else None,
                            scratch_guard=scratch_guard)
        active['pass_digests'].append({'member': key, 'pass': index, 'sha256': value})
        return value
    finally:
        metric(current_pass + ':complete_read', wall, cpu)
        current_pass = prior

def wrap_lease(method):
    original = getattr(scratch._Lease, method)
    def timed(self, *arguments, **keywords):
        if active is None:
            return original(self, *arguments, **keywords)
        wall, cpu = time.monotonic(), time.process_time()
        if method == 'cleanup' and 'deadline_at_monotonic' in active:
            active['seconds_remaining_before_cleanup'] = active['deadline_at_monotonic'] - wall
        try:
            value = original(self, *arguments, **keywords)
            if method == 'measure':
                active['peak_native_scratch_occupied_bytes'] = max(active['peak_native_scratch_occupied_bytes'], value)
            return value
        finally:
            metric('lease_' + method, wall, cpu)
    setattr(scratch._Lease, method, timed)

snapshot.os = OsProxy()
snapshot.hashlib = HashModuleProxy()
snapshot.sqlite3 = SqliteProxy()
snapshot._read = timed_read
for method in ('acquire', 'measure', 'check', 'capacity', 'create', 'cleanup', 'close'):
    wrap_lease(method)
gc.callbacks.append(gc_callback)

def capture_case(name):
    global active, gc_scope
    active = {'name': name, 'operations': {}, 'read_passes': {}, 'pass_digests': [],
              'peak_native_scratch_occupied_bytes': 0, 'query_seconds': .25,
              'gc_count_before': gc.get_count(), 'gc_stats_before': gc.get_stats(), 'returned_success': False}
    case = active
    gc_scope = name + ':PRE_CUSTODY'
    case_source_before = source_custody_snapshot(DATABASE)
    wall, cpu = time.monotonic(), time.process_time()
    deadline = wall + .25
    case['started_at_monotonic'] = wall
    case['deadline_at_monotonic'] = deadline
    gc_scope = name + ':CAPTURE'
    case['deadline_started_before_source_connection'] = True
    try:
        with source_connection(DATABASE, deadline=deadline) as (connection, source_info):
            case['private_connection_entered_wall_seconds'] = time.monotonic() - wall
            case['source_original_identity'] = {'device': source_info.st_dev, 'inode': source_info.st_ino}
            row = connection.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
            case['native_safety_row'] = list(row)
            assert tuple(row) == ('PRODUCTION_PAPER', 0)
        case['returned_success'] = True
    except Exception as error:
        case['error'] = {'class': type(error).__name__,
                         'code': str(error) if isinstance(error, scratch.SnapshotError) else 'NON_SNAPSHOT_DIAGNOSTIC_ERROR'}
    case['total_wall_seconds'] = time.monotonic() - wall
    case['total_cpu_seconds'] = time.process_time() - cpu
    case['returned_after_deadline'] = time.monotonic() >= deadline
    case['gc_count_after'] = gc.get_count()
    case['gc_stats_after'] = gc.get_stats()
    gc_scope = name + ':POST_CUSTODY'
    case['source_custody_unchanged'] = case_source_before == source_custody_snapshot(DATABASE)
    case['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    active = None
    gc_scope = 'BETWEEN_CASES'
    return case

result = {'schema': 'rc6.source-capture-component-diagnostic.v1',
          'diagnostic_only': True, 'acceptance_complete': False, 'artifact_validated': False, 'runtime_validated': False,
          'source_sha': namespace['PIN']['source_sha'], 'source_tree': namespace['PIN']['source_tree'],
          'overlay_count': 0, 'source_files': len(code_before), 'source_index_sha256': hashlib.sha256((ORIGINAL_RAW / 'source.index.json').read_bytes()).hexdigest(),
          'database': str(DATABASE), 'source_bytes': source_before['']['st_size'],
          'interpreter': sys.executable, 'distribution_count': len(installed),
          'preflight_exact_names_versions_and_locks': True, 'query_seconds': .25,
          'scratch_configuration': settings, 'cases': [],
          'gc_policy_changed': False, 'instrumentation': 'TIMING_PROXIES_ONLY_ORIGINAL_READ_COPY_HASH_GUARDS_AND_DEADLINE',
          'instrumentation_limit': 'Callbacks/timing add overhead; no acceptance/throughput claim. Family holding omits native planner holdings.',
          'source_custody_scope': list(source_before[''])}
os.nice(10)
result['process_nice'] = os.getpriority(os.PRIO_PROCESS, 0)
result['command'] = [sys.executable, '-B', '-u', str(Path(__file__).absolute())]
begin = time.monotonic()
try:
    result['cases'].append(capture_case('ISOLATED_CAPTURE_NO_NATIVE_REPORT_HOLDINGS'))
    gc_scope = 'NATIVE_FAMILY_HOLD_PREPARATION'
    family_wall, family_cpu = time.monotonic(), time.process_time()
    inputs = read_runtime(DATABASE, as_of=PRE, row_limit=20000, query_budget_seconds=.5)
    held_family = family_reports(DATABASE, as_of=PRE, catalog=inputs['full_catalog'], sources={})
    result['family_hold'] = {'catalog': len(inputs['full_catalog']), 'instruments': len(held_family['instruments']),
                             'wall_seconds': time.monotonic() - family_wall, 'cpu_seconds': time.process_time() - family_cpu,
                             'source_as_of': PRE.isoformat(), 'planner_holdings_included': False}
    result['cases'].append(capture_case('CAPTURE_WITH_NATIVE_FAMILY_REPORT_HELD'))
except Exception as error:
    result['preparation_error'] = {'class': type(error).__name__,
                                  'code': str(error) if isinstance(error, scratch.SnapshotError) else 'NON_SNAPSHOT_DIAGNOSTIC_ERROR'}
finally:
    gc.callbacks.remove(gc_callback)
    result['gc_events'] = gc_events
    result['provider_requests'] = len(network_attempts)
    result['source_sqlite_connections'] = sum(Path(path) == DATABASE for path in connection_targets)
    result['private_sqlite_connections'] = len(connection_targets)
    result['source_custody_unchanged'] = source_before == source_custody_snapshot(DATABASE)
    result['whole_source_files_and_modes_unchanged'] = code_before == namespace['inventory']()
    result['residual_sessions'] = len([path for path in scratch_root.iterdir() if path.name != scratch.LOCK])
    result['elapsed_wall_seconds'] = time.monotonic() - begin
    result['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    imports = {name: str(Path(module.__file__).absolute()) for name, module in sys.modules.items()
               if getattr(module, '__file__', None) and str(Path(module.__file__).absolute()).startswith(str(SOURCE) + '/')}
    result['source_product_imports'] = imports
    (RAW / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'receipt': str(RAW / 'result.json'), 'cases': len(result['cases']),
                      'source_unchanged': result['source_custody_unchanged'],
                      'whole_code_unchanged': result['whole_source_files_and_modes_unchanged'],
                      'preparation_error': result.get('preparation_error'), 'elapsed': result['elapsed_wall_seconds']}), flush=True)
