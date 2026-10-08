"""Real pytest registration and owned native filesystem lifecycle controls.

Tiny Git archives below are explicit metadata-fixture inputs only. They do not
certify product/runtime/artifact Source. The registration test observes the
actual complete Source used by the unchanged product, health and prepared
modules; it constructs no additional full Source copy.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import tarfile

import pytest

from tests.rc6_browser_ipc import protected_bytes
from tests.rc6_readonly_complete_archive_fixture import ArchiveRegistration, ReadonlyArchive

pytest_plugins = ("tests.rc6_readonly_complete_archive_fixture",)


def test_real_pytest_central_definition_deduplicates_readonly_modules_and_keeps_ipc_independent(
        request, rc6_archive_registration, rc6_readonly_complete_archive):
    """Observe actual FixtureDefs/returned roots, not a fabricated registry."""
    modules = {}
    for item in request.session.items:
        module = item.getparent(pytest.Module)
        if module is not None:
            modules[Path(module.path).name] = module
    expected = ("test_rc6_browser_family_health_coverage.py", "test_rc6_browser_prepared.py")
    definitions = []
    for name in expected:
        assert name in modules, "The two readonly consumer modules must be collected intact"
        central = request._fixturemanager.getfixturedefs("rc6_readonly_complete_archive", modules[name])
        alias = request._fixturemanager.getfixturedefs("complete_archive", modules[name])
        assert len(central) == len(alias) == 1
        assert central[0].scope == "session" and alias[0].scope == "module"
        assert alias[0].argnames == ("request", "rc6_readonly_complete_archive")
        definitions.append(central[0])
    assert definitions[0] is definitions[1]
    assert definitions[0].cached_result[0] is rc6_readonly_complete_archive
    assert request.config.pluginmanager.hasplugin("tests.rc6_readonly_complete_archive_fixture")
    assert rc6_readonly_complete_archive.state == "READY"
    observations = rc6_archive_registration.readonly_leases
    assert {row["module"].rsplit(".", 1)[-1] + ".py" for row in observations} == set(expected)
    assert {row["provider_id"] for row in observations} == {id(rc6_readonly_complete_archive)}
    roots = {(row["root_all11_at_owned_boundary"]["st_dev"],
              row["root_all11_at_owned_boundary"]["st_ino"]) for row in observations}
    assert len(roots) == 1
    assert len(rc6_archive_registration.ipc_roots) == 1
    original = rc6_archive_registration.ipc_roots[0]
    assert original["scope"] == "module"
    assert (original["root_all11_at_owned_setup"]["st_dev"],
            original["root_all11_at_owned_setup"]["st_ino"]) not in roots
    assert len(rc6_readonly_complete_archive.paths) == len(rc6_readonly_complete_archive.index_record["files"])
    assert all(row["status"] == "SOURCE10_CLOSED" for row in observations)


@pytest.fixture
def owned_tiny_archive(tmp_path):
    """Complete native Git/tree for this tiny explicit metadata input only."""
    repository = tmp_path / "native-control-repository"
    repository.mkdir()
    environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    def git(*arguments, binary=False):
        return subprocess.run(["git", "-C", str(repository), *arguments], check=True,
            capture_output=True, timeout=90, env=environment).stdout if binary else subprocess.run(
            ["git", "-C", str(repository), *arguments], check=True, capture_output=True,
            text=True, timeout=90, env=environment).stdout.strip()
    git("init", "--template=", "-q")
    git("config", "user.name", "Explicit native metadata fixture")
    git("config", "user.email", "fixture@example.invalid")
    for name, value in (("caller.py", "VALUE = 1\n"), ("requirements.lock.txt", "sample==1\n"),
                        ("requirements.build.lock.txt", "sample==1\n")):
        (repository / name).write_text(value)
        (repository / name).chmod(0o644)
    git("add", "-A")
    git("commit", "-qm", "Explicit native source-metadata input")
    base = tmp_path / "owned-archive"
    base.mkdir(mode=0o700)
    archive, root, index = base / "source.tar", base / "source", base / "source.index.json"
    root.mkdir()
    archive.write_bytes(git("archive", "HEAD", binary=True))
    with tarfile.open(archive) as members:
        members.extractall(root, filter="data")
    modes, blobs = {}, {}
    for member in git("ls-tree", "-rz", "HEAD", binary=True).split(b"\0"):
        if member:
            metadata, name = member.split(b"\t", 1)
            mode, kind, blob = metadata.decode().split()
            assert kind == "blob"
            modes[name.decode()], blobs[name.decode()] = mode, blob
    commit = git("cat-file", "commit", "HEAD", binary=True)
    (base / "source.commit.raw").write_bytes(commit)
    tree = git("rev-parse", "HEAD^{tree}")
    index.write_text(json.dumps({"schema": "rc6.complete-archive-source-pin.v1",
        "source_sha": git("rev-parse", "HEAD"), "source_tree": tree,
        "tar_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "overlay_count": 0,
        "raw_git_commit_sha256": hashlib.sha256(commit).hexdigest(), "modes": modes, "blob_ids": blobs,
        "files": {name: hashlib.sha256(protected_bytes(root / name)).hexdigest() for name in modes}}))
    controls = tmp_path / "owned-lease-controls"
    controls.mkdir(mode=0o700)
    return ReadonlyArchive((root, index, tree), ArchiveRegistration(), controls)


# The child suite is an explicit Source-metadata lifecycle control only. Its
# three-file complete native Git archive is never product/artifact qualification.
_CHILD_CONFTEST = r"""
import atexit
import hashlib
import json
import os
from pathlib import Path
import pytest
from tests.rc6_readonly_complete_archive_fixture import ArchiveRegistration, ReadonlyArchive
pytest_plugins = ("tests.rc6_readonly_complete_archive_fixture",)
ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "control-config.json").read_text())
CASE = CONFIG["case"]
EVENTS = []
PROVIDER = None

@pytest.fixture(scope="session")
def rc6_readonly_complete_archive():
    global PROVIDER
    controls = ROOT / "native-lease-controls"
    controls.mkdir(mode=0o700)
    provider = ReadonlyArchive((Path(CONFIG["source_root"]), Path(CONFIG["source_index"]),
                               CONFIG["source_tree"]), ArchiveRegistration(), controls)
    original = provider._snapshot
    def observed():
        EVENTS.append("actual-source-metadata-snapshot")
        return original()
    provider._snapshot = observed
    PROVIDER = provider  # Test-only observation handle, not a runtime Source cache.
    if CASE == "owner_rebound_before_admission":
        provider.pid += 1
    return provider

@pytest.fixture(scope="module")
def earlier_dependency():
    yield
    EVENTS.append("earlier-dependency-finalizer")
    if CASE == "teardown_after_alias":
        raise RuntimeError("EXPLICIT_REAL_TEARDOWN_AFTER_ALIAS")

@pytest.fixture(scope="module")
def complete_archive(request, rc6_readonly_complete_archive, earlier_dependency):
    provider = rc6_readonly_complete_archive
    def failure_count():
        if CASE == "baseline_exception":
            raise RuntimeError("EXPLICIT_METADATA_COUNTER_FAULT")
        return request.session.testsfailed
    with provider.lease(request.module.__name__, failure_count) as triple:
        yield triple
    EVENTS.append("alias-finished:" + provider.state)

@pytest.fixture(scope="module")
def later_dependency(complete_archive):
    yield
    EVENTS.append("later-dependency-finalizer")
    if CASE == "teardown_before_alias":
        raise RuntimeError("EXPLICIT_REAL_TEARDOWN_BEFORE_ALIAS")
    if CASE == "pending_reuse":
        # This runs after the test body and before alias teardown, so the real
        # assertion is in earlier_dependency below, after alias becomes pending.
        EVENTS.append("pending-reuse-control-armed")

@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if outcome.excinfo is None:
        report = outcome.get_result()
        EVENTS.append("real-report-created:" + report.when + ":" + report.outcome)

@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_teardown(item, nextitem):
    outcome = yield
    if CASE == "pending_reuse" and PROVIDER is not None and PROVIDER.state == "PENDING_REPORT":
        EVENTS.append("actual-pending-reuse-attempt")
        with pytest.raises(RuntimeError, match="^READONLY_SOURCE_UNKNOWN_OR_UNCLOSED$"):
            with PROVIDER.lease("foreign-next-module-before-report", lambda: 0):
                raise AssertionError("Pending Source was reused")

@atexit.register
def emit_control_only():
    provider = PROVIDER
    row = {"schema": "rc6.explicit-pytest-source-metadata-control.v1", "scope":
        "EXPLICIT_SYNTHETIC_METADATA_ONLY_NOT_PRODUCT_SOURCE_OR_KERNEL_FIN",
        "pid": os.getpid(), "case": CASE, "events": EVENTS,
        "provider_present": provider is not None, "source_payload_postread_claimed": False}
    if provider is not None:
        row.update(state=provider.state, lock_locked=provider.lock.locked(),
            pending_lease_present=provider.pending_lease is not None,
            successful_real_module_reports=len(provider.registry.readonly_leases),
            actual_post_source_snapshot_count=EVENTS.count("actual-source-metadata-snapshot"))
    path = ROOT / "child-terminal-control.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(row, stream, sort_keys=True)
        stream.write("\n")
"""
_CHILD_TEST = r"""
from pathlib import Path
import pytest

def test_actual_source_metadata_control(complete_archive, later_dependency, rc6_readonly_complete_archive):
    import conftest
    provider = rc6_readonly_complete_archive
    target = Path(complete_archive[0]) / "caller.py"
    case = conftest.CASE
    if case == "mode":
        target.chmod(0o600)
    elif case == "bytes":
        target.write_text("VALUE = 2\n")
    elif case == "extra_path":
        (provider.root / "unexpected.txt").write_text("not in the complete tiny native tree\n")
    elif case == "failed_call":
        raise RuntimeError("EXPLICIT_REAL_TEST_CALL_FAILURE")
    elif case == "owner_rebound":
        provider.pid += 1
    elif case == "ordinary_code_read":
        assert target.read_text() == "VALUE = 1\n"
    else:
        assert target.is_file()
"""
_CHILD_REUSE = r"""
def test_following_module_requires_actual_closed_previous_report(complete_archive):
    assert complete_archive[0].is_dir()
"""
_SUPERVISOR = r"""
import errno
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import sys
manager_path, source_root, suite, output = map(Path, sys.argv[1:5])
flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC
f = os.open(manager_path, flags)
before = os.fstat(f)
raw = os.read(f, before.st_size + 1)
after = os.fstat(f)
os.close(f)
fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size", "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
assert stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
assert all(getattr(before, field) == getattr(after, field) == getattr(os.lstat(manager_path), field) for field in fields)
assert hashlib.sha256(raw).hexdigest() == "55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8"
manager = runpy.run_path(str(manager_path))  # Actual tracked stdlib supervisor only.
pre = manager["pre_capture_kernel_state"]()
environment = dict(os.environ)
for key in ("PYTHONPATH", "PYTHONHOME"):
    environment.pop(key, None)
environment.update(PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
    HYPOTHESIS_STORAGE_DIRECTORY=str(suite / "hypothesis"), RUNNER_TEMP=str(suite))
progress_path = suite / "native-progress.json"
progress_identity = None
def actual_owned_progress(stage, pid, phase_entered, management_deadline, log_fd):
    # This is the original manager's real callback ABI, not a Path passed as
    # if it were callable. Record control metadata only in this fresh suite.
    global progress_identity
    assert stage in ("started", "poll") and type(pid) is int and pid > 0
    assert suite.stat().st_uid == os.geteuid() and stat.S_IMODE(suite.stat().st_mode) == 0o700
    if progress_identity is not None:
        before_progress = os.lstat(progress_path)
        assert stat.S_ISREG(before_progress.st_mode) and before_progress.st_nlink == 1
        assert (before_progress.st_dev, before_progress.st_ino, before_progress.st_uid,
                before_progress.st_gid, before_progress.st_mode) == progress_identity
    temporary = suite / "native-progress-next.json"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"schema": "rc6.explicit-pytest-metadata-native-progress.v1", "stage": stage,
            "actual_child_pid": pid, "phase_entered_monotonic": phase_entered,
            "management_deadline_monotonic": management_deadline,
            "native_log_bytes_observed_without_payload_read": os.fstat(log_fd).st_size,
            "writer_pid": os.getpid(), "product_or_artifact_qualification_claimed": False}, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if progress_identity is None:
        assert not os.path.lexists(progress_path)
    os.replace(temporary, progress_path)
    value = os.lstat(progress_path)
    progress_identity = (value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode)
assert callable(actual_owned_progress)
command = [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
           "--confcutdir", str(suite), "--basetemp", str(suite / "pytest-owned"),
           str(suite / "test_01_lease.py"), str(suite / "test_02_reuse.py")]
kernel = manager["managed_native_child"](command, source_root, suite / "native-pytest.log", environment, 300,
    terminate_grace=2, progress_poll=5, progress=actual_owned_progress)
closed = all(kernel.get(key) is True for key in ("actual_child_reaped", "kernel_pre_popen_echild_verified",
    "process_group_absent_at_main_reap", "process_group_absent_after_reap", "owned_children_exhaustion_verified",
    "subreaper_activation_readback_verified", "subreaper_restore_attempted", "subreaper_restoration_readback_verified"))
closed = closed and all(kernel.get(key) is False for key in ("timed_out", "late_observed_main_reap_irreversible_red",
    "kernel_wait4_zero_observed_irreversible_red"))
closed = closed and all(kernel.get(key) == [] for key in ("owned_group_signal_observations",
    "residual_descendants_observed", "remaining_owned_children", "supervisor_errors"))
closed = closed and kernel["launcher_management_deadline_seconds"] == 300 and kernel["owned_cleanup_management_bound_seconds"] == 5
closed = closed and kernel["termination_reap_restore_cleanup_seconds"] <= 5
closed = closed and all(row["exit_code"] == 0 for row in kernel["adopted_descendants_reaped"])
post = manager["pre_capture_kernel_state"]() if closed else None
row = {"schema": "rc6.explicit-pytest-metadata-owned-supervisor.v1", "kernel": kernel,
       "kernel_before": pre, "kernel_after": post, "physical_fin_closed": closed,
       "product_Source_or_artifact_qualification_claimed": False}
f = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(f, "w") as stream:
    json.dump(row, stream, sort_keys=True)
    stream.write("\n")
sys.exit(0 if closed else 1)
"""


def _actual_pytest_control(archive, tmp_path, case):
    root = Path(__file__).resolve().parents[1]
    suite = tmp_path / ("actual-pytest-" + case)
    suite.mkdir(mode=0o700)
    for name, source in (("conftest.py", _CHILD_CONFTEST), ("test_01_lease.py", _CHILD_TEST),
                         ("test_02_reuse.py", _CHILD_REUSE), ("supervisor.py", _SUPERVISOR)):
        (suite / name).write_text(textwrap.dedent(source))
    (suite / "control-config.json").write_text(json.dumps({"case": case,
        "source_root": str(archive.root), "source_index": str(archive.index), "source_tree": archive.tree}))
    owned = suite / "outer-control.json"
    process = subprocess.run([sys.executable, "-I", "-B", str(suite / "supervisor.py"),
        str(root / "scripts/rc6_controlled_native_child_manager.py"), str(root), str(suite), str(owned)],
        capture_output=True, timeout=330)
    assert owned.is_file(), f"Actual supervisor failed before control, rc={process.returncode}"
    kernel = json.loads(protected_bytes(owned))
    assert process.returncode == 0 and kernel["physical_fin_closed"] is True
    assert kernel["kernel_after"]["kernel_echild_verified"] is True
    progress = json.loads(protected_bytes(suite / "native-progress.json"))
    assert progress["schema"] == "rc6.explicit-pytest-metadata-native-progress.v1"
    assert progress["actual_child_pid"] == kernel["kernel"]["pid"]
    assert progress["writer_pid"] == kernel["kernel"]["supervisor_pid"]
    assert progress["native_log_bytes_observed_without_payload_read"] >= 0
    assert progress["product_or_artifact_qualification_claimed"] is False
    # No producer output is read until the actual same-PID supervisor proves FIN.
    terminal = json.loads(protected_bytes(suite / "child-terminal-control.json"))
    assert terminal["scope"] == "EXPLICIT_SYNTHETIC_METADATA_ONLY_NOT_PRODUCT_SOURCE_OR_KERNEL_FIN"
    assert terminal["pid"] == kernel["kernel"]["pid"]
    return terminal, kernel, suite


@pytest.mark.parametrize("change", ("mode", "bytes", "extra_path"))
def test_owned_source_mutation_rejects_readonly_lease_and_all_reuse(owned_tiny_archive, tmp_path, change):
    terminal, kernel, suite = _actual_pytest_control(owned_tiny_archive, tmp_path, change)
    assert kernel["kernel"]["returncode"] != 0
    assert terminal["state"] == "UNKNOWN_OR_UNCLOSED"
    assert terminal["successful_real_module_reports"] == 0
    assert terminal["actual_post_source_snapshot_count"] == 2
    assert terminal["lock_locked"] is False
    rows = [json.loads(protected_bytes(path)) for path in (suite / "native-lease-controls").glob("lease-*.json")]
    assert len(rows) == 1 and rows[0]["status"] == "UNKNOWN_OR_UNCLOSED"
    assert rows[0]["fixture_root_removed"] is False


def test_failed_module_veto_precedes_post_snapshot_and_future_reuse(owned_tiny_archive, tmp_path):
    terminal, kernel, _ = _actual_pytest_control(owned_tiny_archive, tmp_path, "failed_call")
    assert kernel["kernel"]["returncode"] != 0
    assert terminal["state"] == "UNKNOWN_OR_UNCLOSED"
    assert terminal["actual_post_source_snapshot_count"] == 1
    assert terminal["successful_real_module_reports"] == 0
    assert terminal["lock_locked"] is False


def test_rebound_owner_context_veto_precedes_post_snapshot_and_future_reuse(owned_tiny_archive, tmp_path):
    terminal, kernel, _ = _actual_pytest_control(owned_tiny_archive, tmp_path, "owner_rebound")
    assert kernel["kernel"]["returncode"] != 0
    assert terminal["state"] == "UNKNOWN_OR_UNCLOSED"
    assert terminal["actual_post_source_snapshot_count"] == 1
    assert terminal["successful_real_module_reports"] == 0
    assert terminal["lock_locked"] is False


def test_actual_ordinary_code_read_records_source11_honestly_without_timestamp_reset(owned_tiny_archive, tmp_path):
    terminal, kernel, suite = _actual_pytest_control(owned_tiny_archive, tmp_path, "ordinary_code_read")
    assert kernel["kernel"]["returncode"] == 0
    assert terminal["state"] == "READY" and terminal["successful_real_module_reports"] == 2
    assert terminal["actual_post_source_snapshot_count"] == 4
    rows = [json.loads(protected_bytes(path)) for path in sorted((suite / "native-lease-controls").glob("lease-*.json"))]
    assert len(rows) == 2
    for row in rows:
        assert row["Source10_unchanged"] is True
        assert row["Source11_unchanged"] is (not row["CODE_atime_observations"])
        assert row["actual_pytest_teardown_report_passed"] is True
        assert row["native_gate_or_artifact_qualification_claimed"] is False
        assert row["fixture_root_removed"] is False
    events = terminal["events"]
    created = events.index("real-report-created:teardown:passed")
    assert events.index("actual-source-metadata-snapshot", created) > created


@pytest.mark.parametrize("case", ("teardown_before_alias", "teardown_after_alias"))
def test_actual_pytest_dependency_teardown_failure_vetoes_source_postread_and_next_module(
        owned_tiny_archive, tmp_path, case):
    terminal, kernel, _ = _actual_pytest_control(owned_tiny_archive, tmp_path, case)
    assert kernel["kernel"]["returncode"] == 1
    assert terminal["state"] == "UNKNOWN_OR_UNCLOSED"
    assert terminal["actual_post_source_snapshot_count"] == 1
    assert terminal["successful_real_module_reports"] == 0
    assert terminal["lock_locked"] is False and terminal["pending_lease_present"] is False
    assert "real-report-created:teardown:failed" in terminal["events"]


def test_actual_baseline_exception_releases_lock_without_reading_source(owned_tiny_archive, tmp_path):
    terminal, kernel, _ = _actual_pytest_control(owned_tiny_archive, tmp_path, "baseline_exception")
    assert kernel["kernel"]["returncode"] == 1
    assert terminal["state"] == "UNKNOWN_OR_UNCLOSED"
    assert terminal["actual_post_source_snapshot_count"] == 0
    assert terminal["lock_locked"] is False and terminal["pending_lease_present"] is False


def test_actual_pending_report_vetoes_premature_reuse_before_any_post_snapshot(owned_tiny_archive, tmp_path):
    terminal, kernel, _ = _actual_pytest_control(owned_tiny_archive, tmp_path, "pending_reuse")
    assert kernel["kernel"]["returncode"] != 0
    assert "actual-pending-reuse-attempt" in terminal["events"]
    assert terminal["state"] == "UNKNOWN_OR_UNCLOSED"
    assert terminal["actual_post_source_snapshot_count"] == 1
    assert terminal["successful_real_module_reports"] == 0
    assert terminal["lock_locked"] is False


def test_actual_rebound_context_before_admission_vetoes_every_source_snapshot(owned_tiny_archive, tmp_path):
    terminal, kernel, _ = _actual_pytest_control(owned_tiny_archive, tmp_path, "owner_rebound_before_admission")
    assert kernel["kernel"]["returncode"] == 1
    assert terminal["state"] == "UNKNOWN_OR_UNCLOSED"
    assert terminal["actual_post_source_snapshot_count"] == 0
    assert terminal["successful_real_module_reports"] == 0
    assert terminal["lock_locked"] is False
