"""Bounded native custody controls; these do not execute or certify full Gov."""
from __future__ import annotations

import errno
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zlib

import pytest

from scripts import rc6_controlled_governed_runner as runner

SOURCE = Path(runner.__file__).absolute().parents[1]


def native(code, *arguments, timeout=15):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    completed = subprocess.run([sys.executable, '-I', '-B', '-c',
        "import sys; sys.path.insert(0, sys.argv[1]); " + code, str(SOURCE), *map(str, arguments)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture
def literal_tree(tmp_path):
    """A tiny actual Git fixture, never a commit or write in the working repo."""
    root = tmp_path/'literal-tree'
    root.mkdir(mode=0o700)
    subprocess.run(['git', '-c', 'init.defaultBranch=fixture', 'init', '--quiet', str(root)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw = b'VALUE = 1\n'
    (root/'fixture.py').write_bytes(raw)
    (root/'fixture.py').chmod(0o644)

    def object_bytes(kind, payload):
        complete = kind+b' '+str(len(payload)).encode()+b'\0'+payload
        identifier = hashlib.sha1(complete).hexdigest()
        location = root/'.git/objects'/identifier[:2]/identifier[2:]
        location.parent.mkdir(exist_ok=True)
        location.write_bytes(zlib.compress(complete))
        return identifier

    blob = object_bytes(b'blob', raw)
    tree = object_bytes(b'tree', b'100644 fixture.py\0'+bytes.fromhex(blob))
    commit = object_bytes(b'commit', b'tree '+tree.encode()+
        b'\nauthor Offline Fixture <fixture@invalid> 1 +0000\ncommitter Offline Fixture <fixture@invalid> 1 +0000\n\nNative fixture\n')
    (root/'.git/refs/heads/fixture').write_text(commit+'\n')
    subprocess.run(['git', '-C', str(root), 'read-tree', 'HEAD'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return root, commit, tree


def test_raw_git_pin_rejects_same_names_with_changed_bytes(literal_tree):
    root, sha, tree = literal_tree
    pin = runner.source_pin(root, sha, tree)
    assert pin['source_sha'] == sha and pin['source_tree'] == tree
    assert set(pin['files']) == {'fixture.py'}
    assert pin['physical_namespace_exact_to_literal_tree'] is True
    (root/'fixture.py').write_bytes(b'VALUE = 2\n')
    with pytest.raises(ValueError, match='TRACKED_SOURCE_CHANGED|TRACKED_BYTES_MODES_BLOB_MISMATCH'):
        runner.source_pin(root, sha, tree)


@pytest.mark.parametrize('addition', ('.hypothesis/constants/generated', 'data/logs/trading_bot.log', '.pytest_cache'))
def test_physical_namespace_cannot_hide_runtime_output_as_ignored_or_empty_directory(literal_tree, addition):
    root, sha, tree = literal_tree
    assert runner.source_pin(root, sha, tree)['overlay_count'] == 0
    location = root/addition
    if addition == '.pytest_cache':
        location.mkdir()
    else:
        location.parent.mkdir(parents=True)
        location.write_bytes(b'fixture-output')
    with pytest.raises(ValueError, match='PHYSICAL_SOURCE_EXTRA_MISSING_IGNORED_OR_EMPTY_DIRECTORY_FORBIDDEN'):
        runner.source_pin(root, sha, tree)


def test_physical_alias_and_code_atime_claims_remain_distinct(literal_tree, tmp_path):
    root, sha, tree = literal_tree
    path = root/'fixture.py'
    before = runner.source_pin(root, sha, tree)
    assert path.read_bytes() == b'VALUE = 1\n'
    after = runner.source_pin(root, sha, tree)
    runner.compare_source(before, after)
    # A read may not advance atime under relatime, and git status may already
    # have observed it. Inject only the readback field to test its disclosure;
    # a simultaneous stable-field change must still be rejected.
    disclosed = deepcopy(after)
    disclosed['files']['fixture.py']['stat_fields']['st_atime_ns'] = before['files']['fixture.py']['stat_fields']['st_atime_ns']+1
    atime = runner.compare_source(before, disclosed)
    assert any(row['path'] == 'fixture.py' and row['kind'] == 'file' for row in atime)
    assert len(runner.STABLE_CODE_FIELDS) == 10 and 'st_atime_ns' not in runner.STABLE_CODE_FIELDS
    disclosed['files']['fixture.py']['stat_fields']['st_ctime_ns'] += 1
    with pytest.raises(ValueError, match='SOURCE_BYTES_MODE_BLOB_OR_STABLE_CUSTODY_CHANGED'):
        runner.compare_source(before, disclosed)
    os.link(path, tmp_path/'outside-hardlink')
    with pytest.raises(ValueError, match='PHYSICAL_SOURCE_ALIAS_OR_NONREGULAR_OR_CUSTODY_FORBIDDEN'):
        runner.physical_namespace(root)


def test_actual_hypothesis_and_eager_logging_outputs_use_fresh_external_namespace(literal_tree, tmp_path):
    root, sha, tree = literal_tree
    output = tmp_path/'external-output'
    output.mkdir(mode=0o700)
    report = native("""
from pathlib import Path
import json, logging.handlers, os
from scripts import rc6_controlled_governed_runner as r
root, output = Path(sys.argv[2]), Path(sys.argv[5])
before = r.source_pin(root, sys.argv[3], sys.argv[4])
namespace = r.phase_namespace(root, output, 'collection')
from hypothesis.configuration import storage_directory
from hypothesis.database import DirectoryBasedExampleDatabase
storage = storage_directory('examples')
storage.create_if_missing()
database = DirectoryBasedExampleDatabase(storage.path)
database.save(b'key', b'controlled-example')
handler = logging.handlers.RotatingFileHandler(Path(os.environ['LOG_DIR'])/'trading_bot.log')
handler.close()
after = r.source_pin(root, sys.argv[3], sys.argv[4])
atime = r.compare_source(before, after)
print(json.dumps({'namespace':namespace, 'hypothesis_database':str(storage.path),
    'code_unchanged':set(before['files']) == set(after['files']), 'atime':atime,
    'log_exists':(Path(namespace['log_dir'])/'trading_bot.log').is_file()}))
""", root, sha, tree, output)
    assert report['code_unchanged'] and report['log_exists']
    assert Path(report['hypothesis_database']).is_relative_to(output)
    assert report['namespace']['writable_source_exclusions_added'] == []
    assert not (root/'.hypothesis').exists() and not (root/'data').exists()
    with pytest.raises(ValueError, match='FRESH_EXTERNAL_PHASE_NAMESPACE_REQUIRED'):
        # No namespace reuse even if a previous phase completed successfully.
        runner.phase_namespace(root, output, 'collection')


def test_kernel_creation_denial_prevents_optional_ipv6_bind_without_overriding_modules():
    report = native("""
import json, socket
from scripts import rc6_controlled_governed_runner as r
original_socket, original_ipv6 = socket.socket, socket.has_ipv6
capability = r.restrict_inet_creation()
observations = {'inet_socket_attempts':[], 'inet_socket_constructor_requests':[],
    'subprocess_executable_counts':{}}
r.install_phase_audit(observations)
import urllib3
from urllib3.util import connection
ipv4 = socket.socket(socket.AF_INET)
ipv4.close()
left, right = socket.socketpair(socket.AF_UNIX)
left.sendall(b'offline')
assert right.recv(7) == b'offline'
left.close(); right.close()
print(json.dumps({'capability':capability,'observations':observations,
    'urllib3_ipv6':connection.HAS_IPV6, 'socket_class_unchanged':socket.socket is original_socket,
    'compiled_ipv6_unchanged':socket.has_ipv6 == original_ipv6, 'urllib3_origin':urllib3.__file__}))
""")
    assert report['capability']['status'] == 'INSTALLED_AND_KERNEL_WITNESSED'
    assert report['capability']['af_unix_ipc_functional']
    assert len(report['capability']['creation_denial_witnesses']) == 1
    assert all(row['errno'] == errno.EAFNOSUPPORT and not row['socket_created']
               for row in report['capability']['creation_denial_witnesses'])
    assert report['socket_class_unchanged'] and report['compiled_ipv6_unchanged']
    assert report['urllib3_ipv6'] is False
    assert report['observations']['inet_socket_attempts'] == []
    if __import__('socket').has_ipv6:
        assert report['observations']['inet_socket_constructor_requests']
    assert report['capability']['transitive_kernel_network_attestation'] is False


def test_reached_bind_connect_and_dns_stay_denied_and_red_even_with_kernel_filter():
    report = native("""
import json, socket
from scripts import rc6_controlled_governed_runner as r
# A genuine unconnected IPv4 descriptor sends no packet. Its existence grants
# no authority: every real operation below is vetoed by the original audit.
retained = socket.socket(socket.AF_INET)
capability = r.restrict_inet_creation()
observations = {'inet_socket_attempts':[], 'inet_socket_constructor_requests':[],
    'subprocess_executable_counts':{}}
r.install_phase_audit(observations)
denials = []
for action in (lambda:retained.bind(('127.0.0.1',0)),
               lambda:retained.connect(('127.0.0.1',9)), lambda:socket.getaddrinfo('127.0.0.1',9)):
    try: action()
    except RuntimeError as error: denials.append(str(error))
retained.close()
print(json.dumps({'denials':denials,'attempts':observations['inet_socket_attempts'],
    'red':bool(observations['inet_socket_attempts'])}))
""")
    assert len(report['denials']) == 3
    assert report['attempts'] == ['socket.bind', 'socket.connect', 'socket.getaddrinfo']
    assert report['red'] is True


def test_filter_installation_denied_by_kernel_cannot_report_capability_installed():
    report = native("""
import ctypes, errno, json, platform
from scripts import rc6_controlled_governed_runner as r
number = {'x86_64':157,'aarch64':167}[platform.machine()]
class F(ctypes.Structure):
    _fields_=[('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint32)]
class P(ctypes.Structure):
    _fields_=[('length',ctypes.c_ushort),('filters',ctypes.POINTER(F))]
# A real first filter vetoes only a subsequent PR_SET_SECCOMP. There is no
# mocked installer or Python capability flag in this negative control.
values=[(0x20,0,0,0),(0x15,0,2,number),(0x20,0,0,16),(0x15,1,0,22),
    (0x06,0,0,0x7fff0000),(0x06,0,0,0x00050000|errno.EPERM)]
filters=(F*len(values))(*(F(*value) for value in values)); program=P(len(values),filters)
libc=ctypes.CDLL(None,use_errno=True)
assert libc.prctl(38,1,0,0,0)==0
assert libc.prctl(22,2,ctypes.byref(program),0,0)==0
try: r.restrict_inet_creation()
except ValueError as error: print(json.dumps({'blocked':True,'reason':str(error)}))
else: raise AssertionError('VETOED_FILTER_REPORTED_INSTALLED')
""")
    assert report == {'blocked': True, 'reason': 'OFFLINE_INET_CREATION_FILTER_NOT_INSTALLED'}


def kernel_phase(tmp_path, body, *, limit=5):
    control = tmp_path/'phase-control.py'
    control.write_text(body)
    return native("""
import json, os
from pathlib import Path
from scripts import rc6_controlled_governed_runner as r
control=Path(sys.argv[2])
report=r.subprocess_phase([sys.executable,'-I','-B',str(control)],control.parent,
    control.parent/'phase.log',dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),float(sys.argv[3]))
r.publish(control.with_suffix('.custody.json'),r.canonical(report))
print(json.dumps(report))
""", control, limit)


def test_owned_tracker_and_child_are_really_finished_before_main_pid_return(tmp_path):
    report = kernel_phase(tmp_path, """
import json, multiprocessing as mp, sys, time
from pathlib import Path
sys.path.insert(0,"""+repr(str(SOURCE))+""")
from scripts import rc6_controlled_governed_runner as r
initial=r.child_infrastructure_snapshot()
semaphore=mp.get_context('spawn').Semaphore(1)
child=mp.get_context('fork').Process(target=time.sleep,args=(.05,))
child.start()
result=r.finalize_child_infrastructure(initial)
Path(__file__).with_suffix('.finalization.json').write_text(json.dumps(result))
assert result['status']=='GREEN' and result['kernel_echild_before_phase_return']
""")
    finalization = json.loads((tmp_path/'phase-control.finalization.json').read_text())
    assert finalization['status'] == 'GREEN' and finalization['finalization_thread_finished']
    assert finalization['joined_children'] and finalization['forced_termination'] is False
    assert any(row['kind'] == 'resource_tracker' for row in finalization['stopped_owned_infrastructure'])
    assert report['returncode'] == 0 and report['actual_child_reaped']
    assert report['kernel_pre_popen_echild_verified'] and report['owned_children_exhaustion_verified']
    assert report['kernel_wait4_zero_observed_irreversible_red'] is False
    assert report['process_group_absent_at_main_reap'] and report['process_group_absent_after_reap']
    assert report['supervisor_errors'] == []


def test_swallowed_stdlib_finalizer_signal_cannot_terminate_child_to_obtain_green(tmp_path):
    report = kernel_phase(tmp_path, """
import json, multiprocessing as mp, os, signal, sys, time
from multiprocessing import util
from pathlib import Path
sys.path.insert(0,"""+repr(str(SOURCE))+""")
from scripts import rc6_controlled_governed_runner as r
initial=r.child_infrastructure_snapshot()
child=mp.get_context('fork').Process(target=time.sleep,args=(30,))
child.start()
# This actual stdlib finalizer swallows the callback's exception. The signal
# must still be vetoed, recorded and RED independently of that exception.
finalizer=util.Finalize(child, os.kill, args=(child.pid,signal.SIGTERM), exitpriority=1)
result=r.finalize_child_infrastructure(initial)
os.kill(child.pid,0)  # A native liveness probe grants no signal authority.
alive=child.is_alive()
Path(__file__).with_suffix('.finalization.json').write_text(json.dumps({
    'result':result,'child_alive_after_veto':alive,'child_pid':child.pid,
    'finalizer_no_longer_active':not finalizer.still_active()}))
assert result['status']=='RED' and alive
# Do not let atexit's unbounded child join hide this RED. The unchanged native
# parent observes and cleans only its own process group as a failed phase.
os._exit(0)
""", limit=20)
    # This observer management window permits publishing the actual ORIGINAL
    # 5s lifecycle timeout. It does not change lifecycle, owned cleanup or any
    # product/resource budget; the original child remains a real 30s child.
    observed = json.loads((tmp_path/'phase-control.finalization.json').read_text())
    finalization = observed['result']
    assert observed['child_alive_after_veto'] and observed['finalizer_no_longer_active']
    assert finalization['status'] == 'RED' and finalization['management_bound_seconds'] == 5
    assert finalization['forced_termination_attempted'] is True and finalization['signal_vetoed'] is True
    assert finalization['forced_termination'] is (False if finalization['finalization_thread_finished'] else None)
    assert finalization['signal_guard_installed_and_witnessed']
    assert finalization['termination_signal_attempts'] == [{'event': 'os.kill',
        'pid_or_pgid': observed['child_pid'], 'signal': __import__('signal').SIGTERM,
        'vetoed_before_syscall': True}]
    assert any(error['reason'] == 'CHILD_FINALIZATION_SIGNAL_ATTEMPT_DENIED'
               for error in finalization['errors'])
    assert finalization['kernel_echild_before_phase_return'] is False
    assert report['kernel_wait4_zero_observed_irreversible_red']
    assert report['owned_group_signal_observations'] and report['owned_children_exhaustion_verified']
    assert report['process_group_absent_after_reap'] and report['owned_cleanup_management_bound_seconds'] == 5
    assert 'CHILD_FINALIZATION_SIGNAL_DENIED_BEFORE_SYSCALL' in (tmp_path/'phase.log').read_text()


def test_inherited_tracker_is_preserved_and_never_reaped_as_owned(tmp_path):
    control = tmp_path/'inherited-control.py'
    control.write_text("""
import json, multiprocessing as mp, os, sys
from pathlib import Path
sys.path.insert(0,"""+repr(str(SOURCE))+""")
from scripts import rc6_controlled_governed_runner as r

def inherited(output):
    initial=r.child_infrastructure_snapshot()
    result=r.finalize_child_infrastructure(initial)
    after=r.child_infrastructure_snapshot()
    Path(output).write_text(json.dumps({'initial':initial,'result':result,'after':after}))

if __name__=='__main__':
    r.phase_namespace("""+repr(str(SOURCE))+""", Path(__file__).parent, 'execution')
    initial=r.child_infrastructure_snapshot()
    context=mp.get_context('spawn'); semaphore=context.Semaphore(1)
    owned=r.child_infrastructure_snapshot()['resource_tracker_pid']
    child=context.Process(target=inherited,args=(sys.argv[1],));child.start();child.join(5)
    assert child.exitcode==0
    os.kill(owned,0)
    assert semaphore.acquire(timeout=1);semaphore.release()
    result=r.finalize_child_infrastructure(initial)
    print(json.dumps({'parent_tracker_stayed_live_and_usable':True,'parent_finalization':result}))
""")
    output = tmp_path/'inherited-result.json'
    completed = subprocess.run([sys.executable,'-I','-B',str(control),str(output)],
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'), text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=15)
    assert completed.returncode == 0, completed.stderr
    parent, inherited = json.loads(completed.stdout), json.loads(output.read_text())
    assert inherited['initial']['resource_tracker_pid'] is None
    assert type(inherited['initial']['resource_tracker_fd']) is int
    assert inherited['after'] == inherited['initial']
    assert inherited['result']['inherited_tracker_preserved']
    assert inherited['result']['stopped_owned_infrastructure'] == []
    assert inherited['result']['kernel_echild_before_phase_return']
    assert parent['parent_tracker_stayed_live_and_usable'] and parent['parent_finalization']['status'] == 'GREEN'


def test_owned_forkserver_protocol_finishes_before_main_pid_return(tmp_path):
    report = kernel_phase(tmp_path, """
import json,multiprocessing as mp,sys,time
from pathlib import Path
sys.path.insert(0,"""+repr(str(SOURCE))+""")
from scripts import rc6_controlled_governed_runner as r
if __name__=='__main__':
    r.phase_namespace(Path("""+repr(str(SOURCE))+"""), Path(__file__).parent, 'execution')
    initial=r.child_infrastructure_snapshot()
    process=mp.get_context('forkserver').Process(target=time.sleep,args=(.05,))
    process.start();process.join(3)
    assert process.exitcode==0
    result=r.finalize_child_infrastructure(initial)
    Path(__file__).with_suffix('.finalization.json').write_text(json.dumps(result))
    assert result['status']=='GREEN'
""")
    finalization = json.loads((tmp_path/'phase-control.finalization.json').read_text())
    assert finalization['status'] == 'GREEN' and finalization['forced_termination'] is False
    assert {row['kind'] for row in finalization['stopped_owned_infrastructure']} == {'forkserver', 'resource_tracker'}
    assert finalization['kernel_echild_before_phase_return']
    assert report['returncode'] == 0 and report['owned_children_exhaustion_verified']
    assert report['kernel_wait4_zero_observed_irreversible_red'] is False
    assert report['process_group_absent_at_main_reap'] and report['supervisor_errors'] == []


def test_kernel_live_orphan_stays_irreversibly_red_even_after_cleanup(tmp_path):
    report = kernel_phase(tmp_path, """
import os,time
if os.fork()==0:
    time.sleep(30)
    os._exit(0)
os._exit(0)
""")
    assert report['actual_child_reaped'] and report['returncode'] == 0
    assert report['kernel_wait4_zero_observed_irreversible_red']
    assert report['process_group_absent_at_main_reap'] is False
    assert report['residual_descendants_observed'] == ['KERNEL_WAIT4_ZERO_OBSERVED']
    assert report['owned_children_exhaustion_verified'] and report['process_group_absent_after_reap']
    assert report['owned_group_signal_observations']
    assert report['owned_cleanup_management_bound_seconds'] == 5


def test_native_watchdog_does_not_accept_a_phase_that_reaps_after_its_deadline(tmp_path):
    report = kernel_phase(tmp_path, "import time\ntime.sleep(10)\n", limit=.2)
    assert report['timed_out'] is True and report['late_observed_main_reap_irreversible_red'] is True
    assert report['actual_child_reaped'] and report['owned_children_exhaustion_verified']
    assert report['phase_acceptance_deadline_seconds'] == .2
    assert report['owned_cleanup_management_bound_seconds'] == 5
