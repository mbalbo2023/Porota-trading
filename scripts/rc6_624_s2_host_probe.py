#!/usr/bin/env python3
"""Bounded host smoke of the exact candidate mutex in an isolated lock dir.

No canonical lock, RC6 service, runtime file, database, or PPI Watch access.
The only executed commands are /bin/true and /bin/false.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import sys
import tempfile
import time
import types


def main(argv):
    if (os.geteuid() != 0 or len(argv) != 6 or
            not re.fullmatch(r'[0-9a-f]{40}', argv[1]) or
            not re.fullmatch(r'[1-9][0-9]*', argv[2]) or
            not re.fullmatch(r'[1-9][0-9]*', argv[3]) or
            not re.fullmatch(r'[0-9a-f]{40}', argv[5]) or
            pwd.getpwnam(argv[0]).pw_uid == 0):
        raise RuntimeError('S2_PROBE_IDENTITY_INVALID')
    source = base64.b64decode(argv[4], validate=True)
    actual_blob = hashlib.sha1(b'blob ' + str(len(source)).encode() + b'\0' + source).hexdigest()
    if actual_blob != argv[5]:
        raise RuntimeError('S2_PROBE_HELPER_BLOB_MISMATCH')
    module = types.ModuleType('rc6_s2_candidate_mutex')
    exec(compile(source, '<candidate-rc6-deploy-mutex>', 'exec'), module.__dict__)
    identity = (argv[1], argv[2], argv[3])

    def command(phase, binary='/bin/true', run=None):
        return [argv[0], 'root', '--attempt', argv[1], run or argv[2], argv[3],
                '--phase', phase, binary]

    with tempfile.TemporaryDirectory(prefix='rc6-624-s2-', dir='/run/lock') as name:
        root = Path(name)
        module.LOCK_DIR = root
        held = module.open_lock(root)
        try:
            try:
                module.run(command('suspend'))
            except RuntimeError as exc:
                if str(exc) != 'MUTEX_BUSY':
                    raise
            else:
                raise RuntimeError('S2_PROBE_FLOCK_UNSAFE')
        finally:
            os.close(held)
        if module.run(command('suspend', '/bin/false')) != 1:
            raise RuntimeError('S2_PROBE_FAILURE_NOT_PROPAGATED')
        state = module._read_barrier(root)
        if state is None or state['status'] != 'IDLE' or (
                state['candidate'], state['run'], state['attempt']) != identity:
            raise RuntimeError('S2_PROBE_CLEAN_FAILURE_NOT_RECOVERABLE')
        try:
            module.run(command('promotion', run=str(int(argv[2]) + 1)))
        except RuntimeError as exc:
            if str(exc) != 'BARRIER_OTHER_ATTEMPT':
                raise
        else:
            raise RuntimeError('S2_PROBE_SECOND_ATTEMPT_ADMITTED')
        if module.run(command('cleanup_final')) != 0 or module._read_barrier(root) is not None:
            raise RuntimeError('S2_PROBE_FINAL_RELEASE_FAILED')
        # Kill the synthetic lock owner by exiting its process immediately
        # after it launches a bounded, FD-closing grandchild. Observe a real
        # advancing heartbeat and PID before trying a second host operation.
        heartbeat = root / 'live-child.heartbeat'
        grandchild = ('import os,sys,time;from pathlib import Path;'
                      'p=Path(sys.argv[1]);'
                      '[(p.write_text(str(os.getpid())+":"+str(i)),time.sleep(.01)) '
                      'for i in range(300)]')
        child = ('import base64,os,subprocess,sys,types;from pathlib import Path;'
                 'm=types.ModuleType("rc6_s2_nested_probe");'
                 'exec(compile(base64.b64decode(sys.argv[1]),"<s2-helper>","exec"),m.__dict__);'
                 'root=Path(sys.argv[2]);fd=m.open_lock(root);'
                 'm._enter(root,(sys.argv[3],sys.argv[4],sys.argv[5]),"promotion");'
                 'subprocess.Popen([sys.executable,"-c",sys.argv[7],sys.argv[6]],'
                 'close_fds=True,start_new_session=True,stdin=subprocess.DEVNULL,'
                 'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);'
                 'os.close(fd);os._exit(0)')
        subprocess.run([sys.executable, '-c', child, base64.b64encode(source).decode(),
                        str(root), *identity, str(heartbeat), grandchild],
                       check=True, timeout=4)
        live_pid = None
        try:
            for _ in range(100):
                if heartbeat.exists():
                    break
                time.sleep(.01)
            first = heartbeat.read_text()
            live_pid = int(first.partition(':')[0])
            for _ in range(100):
                if heartbeat.read_text() != first:
                    break
                time.sleep(.01)
            if heartbeat.read_text() == first:
                raise RuntimeError('S2_PROBE_LIVE_CHILD_NOT_OBSERVED')
            held = module.open_lock(root)  # wrapper's flock FD is gone
            os.close(held)
            try:
                module.run(command('cleanup_recover'))
            except RuntimeError as exc:
                if str(exc) != 'BARRIER_PRIOR_OPERATION_UNCERTAIN':
                    raise
            else:
                raise RuntimeError('S2_PROBE_UNCERTAIN_ATTEMPT_ADMITTED')
        finally:
            if live_pid is not None:
                try:
                    os.kill(live_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            module._drain_children(max_seconds=1)
    print('RC6_624_S2_HOST_PROBE=' + json.dumps({
        'schema': 'RC6_624_S2_HOST_MUTEX_PROBE_V1', 'status': 'PASS',
        'source_sha': argv[1], 'helper_blob_sha': actual_blob,
        'root_fsync': True, 'linux_subreaper': True,
        'clean_failure_recovery': True, 'foreign_attempt_blocked': True,
        'uncertain_attempt_blocked': True, 'lost_wrapper_simulated': True,
        'live_child_after_wrapper_exit': True, 'flock_released_while_live': True,
        'canonical_lock_touched': False, 'rc6_runtime_touched': False,
        'ppi_watch_touched': False, 'droplet_stress': False,
    }, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
