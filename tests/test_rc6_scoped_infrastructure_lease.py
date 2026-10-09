"""Real tracker birth, hostile ownership and mandatory native G1 probe.

Unavailable kernel census is tested as BLOCKED here; it never qualifies G1.
The canonical Actions receipt must independently prove real positive cleanup.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import porota_predeploy_cleanup as custody
from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts import rc6_heavy_test_preflight as capacity
from scripts import rc6_scoped_infrastructure_lease as infrastructure

ROOT = Path(__file__).absolute().parents[1]


@pytest.mark.parametrize("raw,expected", [(b"", []), (b"123 456 123 \n", [123, 456])])
def test_child_census_preserves_nonempty_ascii_pids(monkeypatch, raw, expected):
    monkeypatch.setattr(infrastructure.os, "listdir", lambda root: ["12", "34"])
    calls = []
    def read(pid, member):
        calls.append((pid, member))
        return raw
    monkeypatch.setattr(infrastructure, "_read_proc", read)
    assert infrastructure.own_kernel_children() == expected
    assert calls == [(os.getpid(), "task/12/children"), (os.getpid(), "task/34/children")]


@pytest.mark.parametrize("raw", [b"0", b"-1", b"+1", b"1x", b"1\x00", b"\xff", "١".encode()])
def test_child_census_invalid_bytes_fail_closed(monkeypatch, raw):
    monkeypatch.setattr(infrastructure.os, "listdir", lambda root: ["12"])
    monkeypatch.setattr(infrastructure, "_read_proc", lambda pid, member: raw)
    with pytest.raises(ValueError, match="INFRA_OWN_CHILD_CENSUS_INVALID"):
        infrastructure.own_kernel_children()


@pytest.mark.parametrize("task", ["0", "-1", "+1", "1x", "١"])
def test_child_census_invalid_task_never_reads_proc(monkeypatch, task):
    monkeypatch.setattr(infrastructure.os, "listdir", lambda root: [task])
    def forbidden(*args):
        raise AssertionError("invalid task must never be read")
    monkeypatch.setattr(infrastructure, "_read_proc", forbidden)
    with pytest.raises(ValueError, match="INFRA_OWN_TASK_INVALID"):
        infrastructure.own_kernel_children()


@pytest.mark.parametrize("case", ["missing", "changed"])
def test_child_census_missing_or_raced_never_becomes_empty(monkeypatch, case):
    tasks = iter((["12"], ["12", "34"]))
    monkeypatch.setattr(infrastructure.os, "listdir", lambda root: next(tasks))
    def read(*args):
        if case == "missing":
            raise FileNotFoundError("controlled absent capability")
        return b"123 "
    monkeypatch.setattr(infrastructure, "_read_proc", read)
    with pytest.raises(ValueError, match="CENSUS_UNAVAILABLE_OR_RACED|TASK_CENSUS_CHANGED"):
        infrastructure.own_kernel_children()


def real_binding():
    def git(value):
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", value], text=True).strip()
    return {"candidate_sha": git("HEAD"), "candidate_tree": git("HEAD^{tree}"),
            "producer": "CONTROLLED_UNIT_FIXTURE_ONLY", "attempt_id": "infra-unit",
            "owner_id": "infra-unit-owner", "runner_class": "DIAGNOSTIC",
            "workload_fingerprint": capacity.digest({"scope": "CONTROLLED_UNIT_INFRA_ONLY"})}


GUARD_CHILD = r'''
import json,os,sys,subprocess,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts.rc6_scoped_infrastructure_lease import SessionResourceTrackerLease,own_kernel_children
from scripts import rc6_controlled_governed_runner as g
from multiprocessing import resource_tracker
config=json.loads(sys.argv[2])
initial=g.child_infrastructure_snapshot()
namespace=lifecycle.create_namespace(Path(config['parent']),config['binding'])
if config['case']=='unknown_tracker': resource_tracker._resource_tracker.ensure_running()
monitor=SessionResourceTrackerLease(config['binding'])
monitor.install()
saved_stderr=sys.stderr
scoped_writer=None
saved_cwd=os.getcwd()
mapped=None
if config['case']=='scoped_fd':
    scoped_writer=open(namespace.path/'owned-writer.raw','w')
    sys.stderr=scoped_writer
if config['case']=='scoped_cwd':os.chdir(namespace.path)
resource_tracker._resource_tracker.ensure_running()
sys.stderr=saved_stderr
if scoped_writer is not None:scoped_writer.close()
if config['case']=='scoped_cwd':os.chdir(saved_cwd)
if config['case']=='scoped_map':
    import mmap
    path=namespace.path/'owned-mapped.raw';path.write_bytes(b'PAPER'*1000)
    with path.open('r+b') as stream:mapped=mmap.mmap(stream.fileno(),0)
tracker=resource_tracker._resource_tracker
saved_pid=tracker._pid
saved_main=resource_tracker.main
extra=None
registered=False
if config['case']=='mutated_pid': tracker._pid+=100000
elif config['case']=='mutated_code':
    def changed_main(*args): return None
    resource_tracker.main=changed_main
elif config['case']=='open_registration':
    tracker.register('/mp-controlled-infra-guard','semaphore');registered=True
elif config['case'] in ('external_child','zombie'):
    program='import time;time.sleep(.3)' if config['case']=='external_child' else 'import sys;sys.exit(7)'
    extra=subprocess.Popen([sys.executable,'-c',program])
    if config['case']=='zombie':time.sleep(.1)
try:
    own_kernel_children();capability=True
except ValueError:capability=False
try:
    lease=monitor.verify_for_fixture(namespace,lifecycle.inventory(namespace))
    outcome={'status':'GREEN','observation':lease.observation}
except (OSError,ValueError,KeyError,TypeError) as error:
    outcome={'status':'BLOCKED','blocker':str(error)}
tracker._pid=saved_pid
resource_tracker.main=saved_main
if mapped is not None:mapped.close()
if registered: tracker.unregister('/mp-controlled-infra-guard','semaphore')
owned_returncode=extra.wait(timeout=5) if extra is not None else None
original_finalizer=g.finalize_child_infrastructure(initial,limit=5)
report={'case':config['case'],'capability_available':capability,'outcome':outcome,
        'monitor':monitor.summary(),'owned_child_returncode':owned_returncode,
        'original_finalizer':original_finalizer,'namespace_preserved':namespace.path.is_dir()}
Path(config['report']).write_text(json.dumps(report))
'''


PROBE_CHILD = r'''
import json,os,sys,hashlib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts import rc6_controlled_governed_runner as g
from scripts.rc6_pytest_fixture_lifecycle import FixtureLifecyclePlugin
config=json.loads(sys.argv[2]);claim=config['claim'];root=Path(claim['path'])
initial=g.child_infrastructure_snapshot()
suite=root/'tiny-suite';suite.mkdir(mode=0o700)
(suite/'state.py').write_text('ROOTS=[]\n')
sys.path.insert(0,str(suite))
source=r"""
def test_tracker_owner(tmp_path):
    from state import ROOTS
    from multiprocessing import get_context
    ROOTS.append(tmp_path)
    (tmp_path/'paper.raw').write_bytes(b'x'*(2*1024**2))
    semaphore=get_context('spawn').Semaphore(1)
    assert semaphore.acquire(timeout=1)
    semaphore.release()
    del semaphore
def test_after_first_real_FIN(tmp_path):
    from state import ROOTS
    assert not ROOTS[0].is_dir()
    ROOTS.append(tmp_path)
    (tmp_path/'paper.raw').write_bytes(b'y'*(2*1024**2))
def test_after_second_real_FIN(tmp_path):
    from state import ROOTS
    assert all(not root.is_dir() for root in ROOTS)
    ROOTS.append(tmp_path)
    (tmp_path/'paper.raw').write_bytes(b'z'*(2*1024**2))
"""
(suite/'test_probe.py').write_text(source)
plugin=FixtureLifecyclePlugin(claim,root/'fixture-controls',candidate_sha=claim['binding']['candidate_sha'],
    candidate_tree=claim['binding']['candidate_tree'])
import pytest
code=pytest.main([str(suite),'-q','-p','no:cacheprovider','--basetemp='+str(root/'fixtures'),
                 '--junitxml='+str(root/'probe-junit.xml')],plugins=[plugin])
before=plugin.summary()
finalizer=g.finalize_child_infrastructure(initial,limit=5)
if finalizer['status']=='GREEN':plugin.retry_after_original_phase_finalization()
after=plugin.summary()
payload={'pytest_exit_code':int(code),'before_original_finalizer':before,'fixture_lifecycle':after,
         'producer_original_finalizer':finalizer,'literal_test_source_sha256':hashlib.sha256(source.encode()).hexdigest()}
(root/'probe-child.json').write_text(json.dumps(payload))
raise SystemExit(0 if int(code)==0 and finalizer['status']=='GREEN' else 1)
'''


PROBE_SUPERVISOR = r'''
import json,os,sys,hashlib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts import rc6_heavy_test_preflight as capacity
from scripts import porota_predeploy_cleanup as custody
config=json.loads(sys.argv[2]);binding=config['binding'];parent=Path(config['parent'])
namespace=lifecycle.create_namespace(parent,binding)
kernel,fin=lifecycle.execute_owned(namespace,[sys.executable,'-I','-B','-c',config['child'],sys.argv[1],
    json.dumps({'claim':lifecycle.namespace_receipt(namespace)})],cwd=namespace.path,
    environ=dict(os.environ,PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1'),
    log_relative='probe-native.log',timeout_seconds=20)
child=json.loads((namespace.path/'probe-child.json').read_text())
required=['probe-native.log','probe-child.json','probe-junit.xml','tiny-suite/test_probe.py']
for path in (namespace.path/'fixture-controls').rglob('*'):
    if path.is_file() and not path.is_symlink():required.append(str(path.relative_to(namespace.path)))
capture=lifecycle.capture_required_evidence(namespace,fin,Path(config['capture_parent'])/'probe-sealed-evidence',required)
source_records=[]
for member in ('scripts/rc6_scoped_infrastructure_lease.py','scripts/rc6_authenticated_fixture_lifecycle.py',
               'scripts/rc6_pytest_fixture_lifecycle.py','scripts/rc6_controlled_governed_runner.py',
               'scripts/rc6_controlled_native_child_manager.py'):
    raw=(Path(sys.argv[1])/member).read_bytes()
    source_records.append({'path':member,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
summary=child['fixture_lifecycle'];before=child['before_original_finalizer'];session=summary['session_infrastructure']
positive=(kernel['returncode']==0 and summary['authenticated_original_factory_scopes']==3
          and summary['closed_and_removed_scopes']==3 and before['closed_and_removed_scopes']==3
          and before['reclaimed_allocated_bytes']>=6*1024**2
          and session['positive_fixture_lease_observations']>0
          and child['producer_original_finalizer']['status']=='GREEN')
cleanup=lifecycle.cleanup_namespace(namespace,fin,capture) if positive else None
report={'schema':'porota.rc6.scoped-infrastructure-probe.v1','binding':binding,
    'source_sha':binding['candidate_sha'],'source_tree':binding['candidate_tree'],
    'python_minor':str(sys.version_info.major)+'.'+str(sys.version_info.minor),
    'python_version':'.'.join(map(str,sys.version_info[:3])),
    'status':'GREEN' if positive else 'BLOCKED','actual_positive':positive,
    'kernel_census_verified':positive,'namespace_removed':bool(cleanup and cleanup['namespace_removed']),
    'native_owned_fin':kernel,'producer_original_finalizer':child['producer_original_finalizer'],
    'fixture_lifecycle':summary,'intrafase_before_original_finalizer':before,
    'source_records':source_records,'raw_required_hashes':[{'path':row['relative_source'],
        'sha256':row['sha256'],'bytes':row['bytes'],'capture_file':row['capture_file']} for row in capture.files],
    'capture_manifest_sha256':capture.manifest_sha256,'capture_path':str(capture.path),
    'cleanup':cleanup,'whole_phase_or_big_ECHILD_guard_changed':False,
    'original_tests_or_fixture_results_changed':False,'real_orders_sent':0}
Path(config['report']).write_text(json.dumps(report))
'''


def run_guard(tmp_path, case):
    parent = tmp_path / case
    parent.mkdir(mode=0o700)
    report = parent / "guard.json"
    config = {"parent": str(parent), "report": str(report), "binding": real_binding(), "case": case}
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", GUARD_CHILD, str(ROOT), json.dumps(config)],
        cwd=parent, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"),
        capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(report.read_text())


@pytest.mark.parametrize("case,signature", [("mutated_pid", "POSITIVE_TRACKER_BIRTH"),
    ("mutated_code", "ORIGINAL_STDLIB_FUNCTION_CHANGED"), ("open_registration", "REGISTRATIONS_NOT_CLOSED"),
    ("unknown_tracker", "POSITIVE_TRACKER_BIRTH"), ("scoped_fd", "FIXTURE_NAMESPACE_HANDLE"),
    ("scoped_cwd", "CWD_ROOT_OR_EXEC_REFERENCES"), ("scoped_map", "REFERENCES_FIXTURE_NAMESPACE")])
def test_real_tracker_ownership_mutation_resources_and_unobserved_birth_fail_closed(tmp_path, case, signature):
    report = run_guard(tmp_path, case)
    assert report["outcome"]["status"] == "BLOCKED"
    assert signature in report["outcome"]["blocker"]
    assert report["namespace_preserved"] is True
    assert report["original_finalizer"]["status"] == "GREEN"


@pytest.mark.parametrize("case", ["external_child", "zombie"])
def test_real_unknown_workload_child_and_zombie_never_gain_tracker_lease_or_lose_reap(tmp_path, case):
    report = run_guard(tmp_path, case)
    assert report["outcome"]["status"] == "BLOCKED"
    assert report["namespace_preserved"] is True
    assert report["owned_child_returncode"] == (7 if case == "zombie" else 0)
    assert report["original_finalizer"]["status"] == "GREEN"


def test_real_native_tracker_birth_census_and_intrafase_cleanup_probe(tmp_path):
    binding = real_binding()
    destination = os.environ.get("RC6_SCOPED_INFRASTRUCTURE_PROBE_DIR")
    if destination:
        claim = json.loads(os.environ["RC6_GOV_AUTHENTICATED_ROOT_JSON"])
        owner = lifecycle.validate_consumer_receipt(claim, candidate_sha=binding["candidate_sha"],
                                                   candidate_tree=binding["candidate_tree"])
        directory = Path(destination).absolute()
        custody.require(directory.is_relative_to(owner) and not os.path.lexists(directory),
                        "INFRA_PROBE_OUTPUT_FRESH_AUTHENTICATED_PATH_REQUIRED")
        directory.mkdir(mode=0o700)
        binding = claim["binding"]
        report = directory / "probe.json"
    else:
        report = tmp_path / "probe.json"
    parent = tmp_path / "native-probe-owner"
    parent.mkdir(mode=0o700)
    config = {"parent": str(parent), "report": str(report), "binding": binding, "child": PROBE_CHILD,
              "capture_parent": str(report.parent)}
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", PROBE_SUPERVISOR, str(ROOT), json.dumps(config)],
        cwd=parent, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"),
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads(report.read_text())
    assert receipt["source_sha"] == binding["candidate_sha"] and receipt["source_tree"] == binding["candidate_tree"]
    assert receipt["producer_original_finalizer"]["status"] == "GREEN"
    assert receipt["native_owned_fin"]["actual_child_reaped"] is True
    assert receipt["native_owned_fin"]["owned_children_exhaustion_verified"] is True
    try:
        infrastructure.own_kernel_children()
    except ValueError as error:
        assert "CENSUS_UNAVAILABLE" in str(error)
        assert receipt["status"] == "BLOCKED" and receipt["actual_positive"] is False
        assert receipt["namespace_removed"] is False and receipt["cleanup"] is None
    else:
        assert receipt["status"] == "GREEN" and receipt["actual_positive"] is True
        assert receipt["kernel_census_verified"] is True and receipt["namespace_removed"] is True
        assert receipt["fixture_lifecycle"]["session_infrastructure"]["positive_fixture_lease_observations"] > 0
    assert receipt["whole_phase_or_big_ECHILD_guard_changed"] is False
