from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import time

os.umask(0o022)
plan_path = Path('/tmp/rc6_archive_v3_maximum_member_source_plan_d9e7fb7d_wait4.json')
plan_raw = plan_path.read_bytes()
plan = json.loads(plan_raw)
probe = Path(plan['external_probe']['path'])
runner_raw = Path(__file__).read_bytes()
assert hashlib.sha256(runner_raw).hexdigest() == plan['external_runner']['sha256']
assert hashlib.sha256(probe.read_bytes()).hexdigest() == plan['external_probe']['sha256']
assert sys.version_info[:3] == (3, 11, 16) and os.geteuid() == 1000
assert len(list(importlib.metadata.distributions())) == 157
assert sys.platform == 'linux' and all(hasattr(os, name) for name in ('wait4', 'waitstatus_to_exitcode', 'killpg'))
assert shutil.which('git')
assert hashlib.sha256(Path(plan['frozen_whole_source']['source_index']).read_bytes()).hexdigest() == plan['frozen_whole_source']['index_sha256']
raw = Path('/tmp/rc6-native-maximum-member-d9e7fb7d-wait4-raw')
fixture = Path('/workspace/rc6-native-maximum-member-d9e7fb7d-wait4')
assert not raw.exists() and not fixture.exists()
command = plan['execution_command_template']
assert command[0] == sys.executable and command[1:3] == ['-I', '-B']
assert command[command.index('--root') + 1] == str(fixture)
assert command[command.index('--receipt') + 1] == str(raw / 'native-receipt.json')
raw.mkdir(mode=0o700)
before = {
    'schema': 'rc6.maximum-archive-member-native-child-wrapper.v2',
    'source_sha': plan['reviewed_source_sha'], 'source_tree': plan['reviewed_source_tree'],
    'command': command, 'cwd': plan['frozen_whole_source']['source_root'],
    'source_index_sha256': plan['frozen_whole_source']['index_sha256'],
    'probe_sha256': plan['external_probe']['sha256'],
    'runner_sha256': hashlib.sha256(runner_raw).hexdigest(),
    'source_plan_sha256': hashlib.sha256(plan_raw).hexdigest(),
    'umask': '022', 'uid': os.geteuid(), 'python': sys.version,
    'scope': 'NARROW_CANONICAL_PUBLISHER_ARCHIVE_MEMORY_ONLY_NO_BUSINESS_TICKS_HORIZON_IMAGE_RUNTIME',
    'resource_source': 'LINUX_WAIT4_EXACT_NATIVE_CHILD_PID_AND_NATIVE_CHILD_RUSAGE_SELF',
    'outer_timeout_seconds': 330, 'executed': False,
    'bootstrap_harness_failure': plan['preserved_prelaunch_failure'],
}
def publish(name, data):
    with (raw / name).open('x') as stream:
        stream.write(json.dumps(data, indent=2, sort_keys=True) + '\n')
publish('wrapper-before.json', before)
start = time.monotonic()
timed_out = False
process = None
try:
    with (raw / 'native.log').open('xb') as log:
        process = subprocess.Popen(command, cwd=before['cwd'],
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        print(json.dumps({'START_PID': process.pid, 'source_sha': before['source_sha'],
                          'command': command, 'resource_source': 'os.wait4'}), flush=True)
        deadline = start + 330
        term_at = kill_at = None
        while True:
            waited_pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if waited_pid:
                assert waited_pid == process.pid
                code = os.waitstatus_to_exitcode(status)
                process.returncode = code
                break
            now = time.monotonic()
            if now >= deadline and term_at is None:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                term_at = now
            elif term_at is not None and now >= term_at + 3 and kill_at is None:
                os.killpg(process.pid, signal.SIGKILL)
                kill_at = now
            elif kill_at is not None and now >= kill_at + 3:
                raise RuntimeError('PRIVATE_NATIVE_CHILD_NOT_REAPED_AFTER_SIGKILL')
            time.sleep(0.1)
    resource_receipt = {
        'schema': 'rc6.native-child-linux-wait4-rusage.v1', 'pid': process.pid,
        'source_sha': before['source_sha'], 'source_tree': before['source_tree'],
        'returncode': code, 'outer_timed_out': timed_out,
        'elapsed_wall_seconds': time.monotonic() - start,
        'cpu_user_seconds': usage.ru_utime, 'cpu_system_seconds': usage.ru_stime,
        'cpu_total_seconds': usage.ru_utime + usage.ru_stime,
        'real_peak_rss_bytes': usage.ru_maxrss * 1024,
        'ru_maxrss_native_unit': 'LINUX_KIBIBYTES',
        'minor_page_faults': usage.ru_minflt, 'major_page_faults': usage.ru_majflt,
        'input_blocks': usage.ru_inblock, 'output_blocks': usage.ru_oublock,
        'voluntary_context_switches': usage.ru_nvcsw, 'involuntary_context_switches': usage.ru_nivcsw,
        'scope': 'EXACT_DIRECT_NATIVE_PYTHON_CHILD_AND_KERNEL_ACCOUNTED_REAPED_DESCENDANTS; RSS_IS_KERNEL_HIGH_WATER_NOT_SUM_OF_SIMULTANEOUS_PROCESSES',
    }
    publish('child-resource.json', resource_receipt)
    final = {**before, 'executed': True, 'native_child_started': True, 'native_child_pid': process.pid,
             'returncode': code, 'outer_timed_out': timed_out,
             'outer_elapsed_wall_seconds': time.monotonic() - start,
             'log_sha256': hashlib.sha256((raw / 'native.log').read_bytes()).hexdigest()}
    for name in ('native-receipt.json', 'child-resource.json'):
        path = raw / name
        if path.exists():
            final[name + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    publish('wrapper-final.json', final)
    print(json.dumps({key: final[key] for key in ('source_sha', 'returncode', 'outer_timed_out',
                                                'outer_elapsed_wall_seconds')}
                     | {'real_peak_rss_bytes': resource_receipt['real_peak_rss_bytes']}), flush=True)
    raise SystemExit(code or int(timed_out))
except Exception as exc:
    publish('wrapper-error.json', {**before, 'executed': process is not None,
                                   'native_child_started': process is not None,
                                   'native_child_pid': process.pid if process is not None else None,
                                   'exception_class': type(exc).__name__, 'reason': str(exc),
                                   'elapsed_wall_seconds': time.monotonic() - start})
    raise
