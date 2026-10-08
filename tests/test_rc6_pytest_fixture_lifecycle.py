"""Real pytest/kernel/FD lifecycle controls with small generated data."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import porota_predeploy_cleanup as custody
from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts import rc6_heavy_test_preflight as capacity

ROOT = Path(__file__).absolute().parents[1]

CASES = {
    "closed": """
def test_first(tmp_path):
    from case_state import remember, FOREIGN
    remember(tmp_path)
    (tmp_path/'owned.bin').write_bytes(b'x'*(2*1024**2))
    (tmp_path/'outside-alias').symlink_to(FOREIGN)
def test_second(tmp_path):
    from case_state import ROOTS, remember
    assert len(ROOTS)==1 and not ROOTS[0].is_dir()
    remember(tmp_path)
    (tmp_path/'owned.bin').write_bytes(b'x'*(2*1024**2))
def test_third(tmp_path):
    from case_state import ROOTS, remember
    assert len(ROOTS)==2 and all(not root.is_dir() for root in ROOTS)
    remember(tmp_path)
    (tmp_path/'owned.bin').write_bytes(b'x'*(2*1024**2))
""",
    "sqlite_writer": """
def test_writer_committed_but_open(tmp_path):
    from case_state import OPEN, remember
    import sqlite3
    remember(tmp_path)
    connection=sqlite3.connect(tmp_path/'source.db')
    connection.execute('CREATE TABLE evidence(value TEXT)')
    connection.execute("INSERT INTO evidence VALUES('PAPER')")
    connection.commit()
    OPEN.append(connection)
""",
    "child": """
def test_owned_child_not_joined(tmp_path):
    from case_state import CHILDREN, remember
    import subprocess, sys
    remember(tmp_path)
    (tmp_path/'owned.raw').write_bytes(b'owned')
    CHILDREN.append(subprocess.Popen([sys.executable,'-c','import time;time.sleep(.3)'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
""",
    "zombie": """
def test_owned_zombie_is_not_reaped_by_fixture_hook(tmp_path):
    from case_state import CHILDREN, remember
    import subprocess, sys, time
    remember(tmp_path)
    child=subprocess.Popen([sys.executable,'-c','import sys;sys.exit(7)'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    CHILDREN.append(child)
    time.sleep(.1)
""",
    "tracker": """
def test_owned_tracker_scope_stays_blocked_until_original_finalizer(tmp_path):
    from case_state import remember
    from multiprocessing import resource_tracker
    remember(tmp_path)
    (tmp_path/'tiny.raw').write_bytes(b'PAPER')
    resource_tracker._resource_tracker.ensure_running()
""",
    "thread": """
def test_owned_writer_thread_not_joined(tmp_path):
    from case_state import THREADS, remember
    import threading, time
    remember(tmp_path)
    worker=threading.Thread(target=time.sleep,args=(.3,))
    worker.start()
    THREADS.append(worker)
""",
    "failed": """
def test_literal_failure(tmp_path):
    from case_state import remember
    remember(tmp_path)
    (tmp_path/'failed-control.raw').write_bytes(b'keep-original-failure-evidence')
    raise AssertionError('CONTROLLED_ORIGINAL_RED_MUST_STAY_RED')
""",
    "external_hardlink": """
def test_external_hardlink_preserved(tmp_path):
    from case_state import remember, FOREIGN
    import os
    remember(tmp_path)
    os.link(FOREIGN,tmp_path/'foreign-link')
""",
    "override": """
import pytest
@pytest.fixture
def tmp_path():
    from case_state import FOREIGN
    return FOREIGN.parent
def test_foreign_fixture_override_is_never_adopted(tmp_path):
    from case_state import FOREIGN
    assert tmp_path==FOREIGN.parent and FOREIGN.read_bytes()==b'foreign-only'
"""
}

CASES["module_shared"] = {
    "conftest.py": """
import hashlib, sqlite3, pytest
@pytest.fixture(scope='session')
def session_source(tmp_path_factory):
    from case_state import remember
    root=tmp_path_factory.mktemp('session-owned-source')
    remember(root)
    (root/'immutable.bin').write_bytes(b's'*(2*1024**2))
    yield root
@pytest.fixture(scope='module')
def shared_source(tmp_path_factory, session_source):
    from case_state import remember
    root=tmp_path_factory.mktemp('module-owned-source')
    remember(root)
    payload=root/'immutable.bin'
    payload.write_bytes(b'm'*(2*1024**2))
    digest=hashlib.sha256(payload.read_bytes()).hexdigest()
    writer=sqlite3.connect(root/'writer.db')
    writer.execute('CREATE TABLE fixture(value TEXT)')
    writer.commit()
    yield root,digest,session_source
    writer.close()
""",
    "test_alpha.py": """
import hashlib
def test_first_consumer_preserves_shared_scope(shared_source):
    root,digest,session_source=shared_source
    assert root.exists() and session_source.exists()
    assert hashlib.sha256((root/'immutable.bin').read_bytes()).hexdigest()==digest
def test_last_consumer_preserves_shared_scope_until_real_teardown(shared_source):
    root,digest,session_source=shared_source
    assert root.exists() and session_source.exists()
    assert hashlib.sha256((root/'immutable.bin').read_bytes()).hexdigest()==digest
""",
    "test_beta.py": """
import hashlib
def test_next_module_sees_only_previous_closed_module_removed(shared_source):
    from case_state import ROOTS
    root,digest,session_source=shared_source
    assert len(ROOTS)==3 and ROOTS[0].is_dir() and not ROOTS[1].is_dir() and ROOTS[2].is_dir()
    assert root.exists() and session_source.exists()
    assert hashlib.sha256((root/'immutable.bin').read_bytes()).hexdigest()==digest
"""
}

CASES["module_teardown_failure"] = {
    "test_case.py": """
import pytest
@pytest.fixture(scope='module')
def original_module(tmp_path_factory):
    from case_state import remember
    root=tmp_path_factory.mktemp('module-red')
    remember(root)
    (root/'original.raw').write_bytes(b'preserve-original-teardown-error')
    yield root
    raise RuntimeError('ORIGINAL_MODULE_TEARDOWN_RED')
def test_module(original_module):
    assert (original_module/'original.raw').exists()
"""
}

CASES["module_parametrized"] = {
    "test_case.py": """
import pytest
@pytest.fixture(scope='module',params=[0,1])
def parameter_root(tmp_path_factory,request):
    from case_state import remember
    root=tmp_path_factory.mktemp('parameter-root')
    remember(root)
    (root/'literal.raw').write_bytes(str(request.param).encode())
    yield root
def test_original_parameters(parameter_root):
    assert parameter_root.exists()
"""
}

CASES["number_sequence"] = """
import pytest
@pytest.mark.parametrize('ordinal',range(12))
def test_original_numbering(ordinal,tmp_path_factory):
    from case_state import remember
    prefix=('instrument','instrument2')[ordinal%2]
    root=tmp_path_factory.mktemp(prefix)
    remember(root)
    (root/'tiny.raw').write_bytes(b'PAPER')
"""

CASES["unnumbered"] = """
def test_original_unnumbered_collision(tmp_path_factory):
    from case_state import remember
    import pytest
    root=tmp_path_factory.mktemp('fixed-owned-root',numbered=False)
    remember(root)
    (root/'tiny.raw').write_bytes(b'PAPER')
def test_original_second_unnumbered_call_still_raises(tmp_path_factory):
    from case_state import ROOTS
    import pytest
    assert ROOTS[0].exists()
    with pytest.raises(FileExistsError):
        tmp_path_factory.mktemp('fixed-owned-root',numbered=False)
"""

CASES["readonly_controls"] = {
    "conftest.py": """
import hashlib,json,os,subprocess,tarfile,pytest
from tests.rc6_readonly_complete_archive_fixture import ReadonlyArchive,ArchiveRegistration
pytest_plugins=['tests.rc6_readonly_complete_archive_fixture']
@pytest.fixture(scope='session')
def readonly_source(tmp_path_factory):
    from case_state import remember
    base=tmp_path_factory.mktemp('tiny-readonly-owned-source')
    remember(base)
    native=base/'tiny-git'
    native.mkdir()
    (native/'demo.py').write_bytes(b'PAPER_SOURCE=1\\n')
    env=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull)
    def git(*args):
        return subprocess.check_output(['git','-C',str(native),*args],env=env)
    git('init','-q')
    git('config','user.email','fixture@example.invalid')
    git('config','user.name','Controlled Source Fixture')
    git('add','demo.py')
    git('commit','-q','-m','authentic tiny fixture only')
    sha=git('rev-parse','HEAD').decode().strip()
    tree=git('rev-parse','HEAD^{tree}').decode().strip()
    archive=base/'source.tar'
    archive.write_bytes(git('archive','HEAD'))
    source=base/'source'
    source.mkdir()
    with tarfile.open(archive) as members: members.extractall(source,filter='data')
    commit=git('cat-file','commit','HEAD')
    (base/'source.commit.raw').write_bytes(commit)
    mode,kind,blob=git('ls-tree','HEAD','demo.py').decode().split()[:3]
    row={'schema':'rc6.complete-archive-source-pin.v1','source_sha':sha,'source_tree':tree,
         'overlay_count':0,'files':{'demo.py':hashlib.sha256((source/'demo.py').read_bytes()).hexdigest()},
         'modes':{'demo.py':mode},'blob_ids':{'demo.py':blob}}
    index=base/'source.index.json'
    index.write_text(json.dumps(row))
    controls=tmp_path_factory.mktemp('tiny-readonly-source-controls')
    remember(controls)
    return ReadonlyArchive((source,index,tree),ArchiveRegistration(),controls)
@pytest.fixture(scope='module')
def source_lease(readonly_source,request):
    with readonly_source.lease(request.module.__name__,lambda:request.session.testsfailed) as triple:
        yield triple
""",
    "test_alpha.py": """
def test_actual_source_lease_first_module(source_lease,readonly_source):
    from case_state import ROOTS
    assert all(root.is_dir() for root in ROOTS)
    assert (source_lease[0]/'demo.py').read_bytes()==b'PAPER_SOURCE=1\\n'
""",
    "test_beta.py": """
def test_actual_last_source_lease_with_unjoined_child(source_lease,readonly_source):
    from case_state import ROOTS,CHILDREN
    import subprocess,sys
    assert all(root.is_dir() for root in ROOTS)
    assert readonly_source.sequence==2
    CHILDREN.append(subprocess.Popen([sys.executable,'-c','import time;time.sleep(.3)']))
"""
}

CHILD = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from scripts.rc6_pytest_fixture_lifecycle import FixtureLifecyclePlugin
import pytest
config=json.loads(sys.argv[2])
parent=Path(config['parent'])
namespace=lifecycle.create_namespace(parent, config['binding'])
claim=lifecycle.namespace_receipt(namespace)
suite=namespace.path/'suite'
suite.mkdir(mode=0o700)
controls=namespace.path/'controls'
foreign=parent/'foreign-preserved.raw'
foreign.write_bytes(b'foreign-only')
foreign_fd=os.open(foreign,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME)
original_foreign=[foreign.stat().st_dev,foreign.stat().st_ino,foreign.stat().st_size,
                  foreign.stat().st_mtime_ns,foreign.stat().st_ctime_ns]
helper="from pathlib import Path\nROOTS=[]\nOPEN=[]\nCHILDREN=[]\nTHREADS=[]\nFOREIGN=Path("+repr(str(foreign))+")\ndef remember(path): ROOTS.append(path)\n"
(suite/'case_state.py').write_text(helper)
sys.path.insert(0,str(suite))
if isinstance(config['source'],dict):
    for name,source in config['source'].items(): (suite/name).write_text(source)
else:
    (suite/'test_case.py').write_text(config['source'])
plugin=FixtureLifecyclePlugin(claim,controls,candidate_sha=config['binding']['candidate_sha'],
                             candidate_tree=config['binding']['candidate_tree'])
rc=pytest.main([str(suite),'-q','-p','no:cacheprovider',
    '--basetemp='+str(namespace.path/'fixtures')],plugins=[] if config.get('disable_plugin') else [plugin])
from case_state import ROOTS,OPEN,CHILDREN,THREADS
before=plugin.summary()
preserved_before=[root.is_dir() for root in ROOTS]
for connection in OPEN: connection.close()
child_returncodes=[child.wait(timeout=5) for child in CHILDREN]
for thread in THREADS: thread.join(5)
plugin.retry_after_original_phase_finalization()
after=plugin.summary()
preserved_after=[root.is_dir() for root in ROOTS]
assert os.fstat(foreign_fd).st_ino==original_foreign[1]
now=[foreign.stat().st_dev,foreign.stat().st_ino,foreign.stat().st_size,
     foreign.stat().st_mtime_ns,foreign.stat().st_ctime_ns]
if config['case']!='external_hardlink': assert now==original_foreign
report={'pytest_exit_code':int(rc),'before':before,'after':after,
        'preserved_before':preserved_before,'preserved_after':preserved_after,
        'foreign_fd_closed_by_plugin':False,'foreign_bytes':foreign.read_bytes().decode(),
        'factory_sequence':[root.name for root in ROOTS],
        'owned_child_returncodes_after_original_wait':child_returncodes,
        'control_files':len(list(controls.rglob('*'))),'scope':'CONTROLLED_UNIT_FIXTURE_ONLY',
        'promotion_or_full_governed_claimed':False}
capture_manifests=[]
for manifest_path in controls.glob('*.capture/manifest.json'):
    manifest=json.loads(manifest_path.read_text())
    capture_manifests.append({'files':[row['relative_source'] for row in manifest['files']],
        'all_payloads_hashed':all(len(row['sha256'])==64 for row in manifest['files'])})
report['capture_manifests']=capture_manifests
os.close(foreign_fd)
Path(config['report']).write_text(json.dumps(report))
"""


def real_binding():
    def git(expression):
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", expression], text=True).strip()
    return {"candidate_sha": git("HEAD"), "candidate_tree": git("HEAD^{tree}"),
            "producer": "CONTROLLED_UNIT_FIXTURE_ONLY", "attempt_id": "native-guard-unit",
            "owner_id": "native-unit-fixture-owner", "runner_class": "DIAGNOSTIC",
            "workload_fingerprint": capacity.digest({"scope": "UNIT_ONLY", "catalog": 0, "observations": 0})}


def run_case(tmp_path, case, *, disable_plugin=False):
    suffix = case + ("-baseline" if disable_plugin else "")
    parent = tmp_path / ("owned-parent-" + suffix)
    parent.mkdir(mode=0o700)
    report = tmp_path / ("literal-report-" + suffix + ".json")
    config = {"case": case, "parent": str(parent), "report": str(report),
              "binding": real_binding(), "source": CASES[case], "disable_plugin": disable_plugin}
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, str(ROOT), json.dumps(config)],
        cwd=parent, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"),
        capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(report.read_text())


def test_real_original_tmp_path_is_removed_before_next_test_without_changing_tests(tmp_path):
    report = run_case(tmp_path, "closed")
    assert report["pytest_exit_code"] == 0
    assert report["after"]["closed_and_removed_scopes"] == 3
    assert report["after"]["preserved_blocked_or_failed_scopes"] == 0
    assert report["after"]["reclaimed_allocated_bytes"] >= 6 * 1024**2
    assert report["preserved_after"] == [False, False, False]
    assert report["foreign_bytes"] == "foreign-only" and report["foreign_fd_closed_by_plugin"] is False
    assert report["after"]["fixtures_or_tests_redefined"] is False
    assert report["after"]["global_cleanup_claimed"] is False


def test_real_module_and_session_roots_close_after_last_consumer_and_own_writer_teardown(tmp_path):
    report = run_case(tmp_path, "module_shared")
    assert report["pytest_exit_code"] == 0
    assert report["before"]["scope_counts"]["module"] == 2
    assert report["before"]["scope_counts"]["session"] == 1
    assert report["before"]["closed_and_removed_scopes"] == 3
    assert report["before"]["reclaimed_allocated_bytes"] >= 6 * 1024**2
    assert report["preserved_before"] == [False, False, False]
    assert report["after"]["original_factory_context_failures"] == []
    assert report["foreign_bytes"] == "foreign-only" and report["foreign_fd_closed_by_plugin"] is False


def test_real_module_teardown_failure_and_ambiguous_parameter_FIN_are_preserved(tmp_path):
    failed = run_case(tmp_path, "module_teardown_failure")
    assert failed["pytest_exit_code"] == 1
    assert failed["after"]["closed_and_removed_scopes"] == 0
    assert failed["preserved_after"] == [True]
    assert "REAL_PYTEST_PHASE_FAILED" in failed["after"]["rows"][0]["blockers"]
    parameterized = run_case(tmp_path, "module_parametrized")
    assert parameterized["pytest_exit_code"] == 0
    assert parameterized["after"]["scope_counts"]["module"] == 2
    assert parameterized["preserved_after"] == [True, False]
    assert parameterized["after"]["preserved_blocked_or_failed_scopes"] == 1


def test_real_readonly_Source10_leases_are_closed_and_captured_before_shared_roots_retire(tmp_path):
    report = run_case(tmp_path, "readonly_controls")
    assert report["pytest_exit_code"] == 0
    assert report["before"]["scope_counts"]["session"] == 2
    assert report["preserved_before"] == [True, True]
    assert report["preserved_after"] == [False, False]
    assert report["after"]["closed_and_removed_scopes"] == 2
    controls = [manifest for manifest in report["capture_manifests"] if "lease-0001.json" in manifest["files"]]
    assert len(controls) == 1 and "lease-0002.json" in controls[0]["files"]
    assert all(manifest["all_payloads_hashed"] for manifest in report["capture_manifests"])


def test_original_numbered_factory_sequence_matches_baseline_and_unnumbered_collision_is_preserved(tmp_path):
    baseline = run_case(tmp_path, "number_sequence", disable_plugin=True)
    owned = run_case(tmp_path, "number_sequence")
    assert baseline["pytest_exit_code"] == owned["pytest_exit_code"] == 0
    assert baseline["factory_sequence"] == owned["factory_sequence"]
    assert owned["before"]["closed_and_removed_scopes"] == 12
    assert owned["preserved_before"] == [False] * 12
    assert baseline["preserved_before"] == [True] * 12
    fixed = run_case(tmp_path, "unnumbered")
    assert fixed["pytest_exit_code"] == 0
    assert fixed["preserved_after"] == [True]
    assert "UNNUMBERED_FACTORY_NAMESPACE_COLLISION_SEMANTICS" in " ".join(fixed["after"]["rows"][0]["blockers"])


@pytest.mark.parametrize("case, signature", [("sqlite_writer", "OPEN_FILE_OR_SQLITE_WRITER"),
    ("child", "PRE_SOURCE_OWN_CHILD_PRESENT"), ("thread", "LIVE_WRITER_THREAD")])
def test_real_open_writer_child_and_thread_preserve_root_until_actual_close(tmp_path, case, signature):
    report = run_case(tmp_path, case)
    assert report["pytest_exit_code"] == 0
    assert report["before"]["closed_and_removed_scopes"] == 0
    assert report["preserved_before"] == [True]
    assert signature in " ".join(report["before"]["rows"][0]["blockers"])
    assert report["after"]["closed_and_removed_scopes"] == 1
    assert report["preserved_after"] == [False]
    assert report["foreign_fd_closed_by_plugin"] is False


def test_own_zombie_reap_is_preserved_and_persistent_tracker_is_not_stopped(tmp_path):
    zombie = run_case(tmp_path, "zombie")
    assert zombie["pytest_exit_code"] == 0
    assert zombie["preserved_before"] == [True] and zombie["preserved_after"] == [False]
    assert zombie["owned_child_returncodes_after_original_wait"] == [7]
    assert "WNOWAIT_PRECHECK_NO_REAP" in " ".join(zombie["before"]["rows"][0]["blockers"])
    tracker = run_case(tmp_path, "tracker")
    assert tracker["pytest_exit_code"] == 0
    from scripts.rc6_scoped_infrastructure_lease import own_kernel_children
    try:
        own_kernel_children()
    except ValueError as error:
        assert "CENSUS_UNAVAILABLE" in str(error)
        assert tracker["before"]["closed_and_removed_scopes"] == 0
        assert tracker["preserved_after"] == [True]
        assert "CENSUS_UNAVAILABLE" in " ".join(tracker["after"]["rows"][0]["blockers"])
    else:
        assert tracker["before"]["closed_and_removed_scopes"] == 1
        assert tracker["preserved_after"] == [False]
        assert tracker["after"]["session_infrastructure"]["positive_fixture_lease_observations"] > 0
    assert tracker["after"]["session_infrastructure"]["global_finalizer_or_stop_called"] is False


def test_real_failed_report_preserves_original_failure_and_namespace(tmp_path):
    report = run_case(tmp_path, "failed")
    assert report["pytest_exit_code"] == 1
    assert report["after"]["closed_and_removed_scopes"] == 0
    assert report["preserved_after"] == [True]
    assert report["after"]["rows"][0]["status"] == "UNKNOWN_OR_FAILED_PRESERVED"


def test_external_hardlink_and_custom_fixture_are_never_deleted(tmp_path):
    linked = run_case(tmp_path, "external_hardlink")
    assert linked["pytest_exit_code"] == 0 and linked["preserved_after"] == [True]
    assert "EXTERNAL_HARDLINK_BLOCKED" in linked["after"]["rows"][0]["blockers"][0]
    other = tmp_path / "override-case"
    other.mkdir()
    overridden = run_case(other, "override")
    assert overridden["pytest_exit_code"] == 0
    assert overridden["after"]["actual_original_function_tmp_path_scopes"] == 0
    assert overridden["foreign_bytes"] == "foreign-only"


def test_nonempty_or_foreign_fixture_cannot_be_adopted(tmp_path):
    namespace = lifecycle.create_namespace(tmp_path, real_binding())
    controls = namespace.path / "controls"
    controls.mkdir(mode=0o700)
    fixture = namespace.path / "fixture"
    fixture.mkdir(mode=0o700)
    (fixture / "preserved.raw").write_bytes(b"preexisting data")
    with pytest.raises(custody.CleanupRejected, match="NOT_FRESH_EMPTY"):
        lifecycle.adopt_empty_fixture_namespace(fixture, real_binding(), control_parent=controls,
            parent_claim=lifecycle.namespace_receipt(namespace), nodeid="tests/test_control.py::test_case")
    assert (fixture / "preserved.raw").read_bytes() == b"preexisting data"
    foreign = tmp_path / "foreign-empty"
    foreign.mkdir(mode=0o700)
    with pytest.raises(custody.CleanupRejected, match="FRESH_PYTEST_SCOPE_REQUIRED"):
        lifecycle.adopt_empty_fixture_namespace(foreign, real_binding(), control_parent=controls,
            parent_claim=lifecycle.namespace_receipt(namespace), nodeid="tests/test_control.py::test_case")

