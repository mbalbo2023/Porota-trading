import importlib
import importlib.util
import errno
import socket
import stat
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest


def module():
    name = "scripts.rc6_ci_ws12_h5_runner"
    assert importlib.util.find_spec(name) is not None
    return importlib.import_module(name)


class Connection:
    def __init__(self, pid, uid):
        self.credentials = struct.pack("3i", pid, uid, 1)
        self.sent = []

    def getsockopt(self, level, option, length):
        assert (level, option, length) == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return self.credentials

    def sendall(self, data):
        self.sent.append(data)


def test_foreign_peer_cannot_receive_even_a_synthetic_order():
    runner = module()
    conn = Connection(pid=700, uid=1000)
    with pytest.raises(runner.RunnerHold):
        runner._send_checked(conn, fixture_pid=701, fixture_uid=1000,
                             packet=b'{"op":"place_order","consumer":"watch"}\n')
    assert conn.sent == []


def test_wrong_uid_or_malformed_credentials_cannot_receive_order():
    runner = module()
    for conn in (Connection(pid=701, uid=1001), Connection(pid=701, uid=1000)):
        if conn.credentials == struct.pack("3i", 701, 1000, 1):
            conn.credentials = b"bad"
        with pytest.raises(runner.RunnerHold):
            runner._send_checked(conn, fixture_pid=701, fixture_uid=1000,
                                 packet=b'{"op":"place_order","consumer":"watch"}\n')
        assert conn.sent == []


def test_no_provenance_returns_fixed_hold_before_fixture_start():
    runner = module()
    result = runner.run({})
    assert result == {"status": "HOLD", "synthetic_fixture_observed": False,
                      "physical_execution_observed": False,
                      "candidate_reported_pass": False,
                      "af_unix_independently_verified": False,
                      "qa_certified": False, "automatic_go": False,
                      "cleanup_status": "NOT_ATTEMPTED"}


def test_matching_kernel_peer_receives_only_fixed_synthetic_payload():
    runner = module()
    conn = Connection(pid=701, uid=1000)
    payload = b'{"op":"place_order","consumer":"watch"}\n'
    runner._send_checked(conn, fixture_pid=701, fixture_uid=1000, packet=payload)
    assert conn.sent == [payload]
    with pytest.raises(runner.RunnerHold):
        runner._send_checked(conn, fixture_pid=701, fixture_uid=1000,
                             packet=b'{"op":"place_order","account":"real"}\n')
    assert conn.sent == [payload]


def _context():
    from scripts.rc6_ci_ws12_main_entry import WORKFLOW_REF
    return {"GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_PROTECTED": "true", "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
            "RUNNER_ENVIRONMENT": "github-hosted", "GITHUB_SHA": "a" * 40,
            "GITHUB_WORKFLOW_SHA": "a" * 40, "RC6_CANDIDATE_SHA": "b" * 40,
            "RC6_CANDIDATE_TREE_SHA": "c" * 40, "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1"}


def test_wrong_checkout_prevents_any_fixture_launch(monkeypatch):
    runner = module()
    monkeypatch.setattr(runner, "_checkout_matches", lambda _: False)
    def forbidden():
        raise AssertionError("unverified source must not start a fixture")
    monkeypatch.setattr(runner, "_fixture_probe", forbidden)
    assert runner.run(_context())["synthetic_fixture_observed"] is False


def test_fixture_timeout_and_foreign_evidence_cannot_set_certification(monkeypatch):
    runner = module()
    monkeypatch.setattr(runner, "_checkout_matches", lambda _: b"reviewed source")
    def timeout(source):
        raise runner.RunnerHold("TIMEOUT")
    monkeypatch.setattr(runner, "_fixture_probe", timeout)
    result = runner.run(_context())
    assert result["status"] == "HOLD"
    assert result["synthetic_fixture_observed"] is False
    assert result["candidate_reported_pass"] is False
    assert result["qa_certified"] is False
    assert result["cleanup_status"] == "DEFERRED_UNVERIFIED"


def test_fixture_receives_exact_provenance_verified_source(monkeypatch):
    runner = module()
    monkeypatch.setattr(runner, "_checkout_matches", lambda _: b"reviewed source")
    received = []
    def probe(source):
        received.append(source)
        return True
    monkeypatch.setattr(runner, "_fixture_probe", probe)
    result = runner.run(_context())
    assert received == [b"reviewed source"]
    assert result["synthetic_fixture_observed"] is True


def test_missing_memfd_support_is_hold_without_certification(tmp_path, monkeypatch):
    runner = module()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    monkeypatch.setattr(runner, "_checkout_matches", lambda _: b"reviewed source")
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(private))
    def unavailable(*args):
        raise AttributeError("memfd unavailable")
    monkeypatch.setattr(runner.os, "memfd_create", unavailable)
    receipt = runner.run(_context())
    assert receipt["status"] == "HOLD"
    assert receipt["synthetic_fixture_observed"] is False
    assert receipt["qa_certified"] is False


def test_synthetic_success_does_not_attest_candidate_or_authorize_release(monkeypatch):
    runner = module()
    monkeypatch.setattr(runner, "_checkout_matches", lambda _: b"reviewed source")
    monkeypatch.setattr(runner, "_fixture_probe", lambda source: True)
    result = runner.run(_context())
    assert result["synthetic_fixture_observed"] is True
    assert result["candidate_reported_pass"] is False
    assert result["physical_execution_observed"] is False
    assert result["af_unix_independently_verified"] is False
    assert result["qa_certified"] is False
    assert result["automatic_go"] is False


def test_owned_fixture_over_real_local_unix_socket():
    runner = module()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
            pass
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EAFNOSUPPORT, errno.ENOSYS}:
            pytest.skip("AF_UNIX unavailable; synthetic physical fixture unverified")
        raise
    with pytest.raises(runner.RunnerHold, match="CLEANUP_DEFERRED_UNVERIFIED"):
        runner._fixture_probe(Path(runner.__file__).read_bytes())


def test_fixture_readiness_timeout_stops_only_owned_child(tmp_path, monkeypatch):
    runner = module()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(private))
    clock = iter(range(100))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    class OwnedChild:
        pid = 701
        terminated = False
        def poll(self):
            return 0 if self.terminated else None
        def terminate(self):
            self.terminated = True
        def wait(self, timeout):
            return 0
    child = OwnedChild()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: child)
    with pytest.raises(runner.RunnerHold, match="TIMEOUT"):
        runner._fixture_probe(b"fixture source")
    assert child.terminated is True
    assert private.exists()


def test_child_source_is_sealed_before_spawn(tmp_path, monkeypatch):
    runner = module()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(private))
    inspected = []
    def inspect_spawn(argv, **kwargs):
        (fd,) = kwargs["pass_fds"]
        assert argv[1:3] == ["-I", "-S"]
        assert argv[3] == f"/proc/self/fd/{fd}"
        assert runner.os.pread(fd, 64, 0) == b"reviewed source"
        with pytest.raises(OSError):
            runner.os.write(fd, b"tamper")
        inspected.append(True)
        raise runner.RunnerHold("TEST_STOP")
    monkeypatch.setattr(runner.subprocess, "Popen", inspect_spawn)
    with pytest.raises(runner.RunnerHold, match="TEST_STOP"):
        runner._fixture_probe(b"reviewed source")
    assert inspected == [True]


def test_fixture_cleanup_failure_cannot_report_success(tmp_path, monkeypatch):
    runner = module()
    monkeypatch.setattr(runner, "_await_ready", lambda process, timeout: None)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    endpoint = private / "fixture.sock"
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(private))
    original_lstat = Path.lstat
    def lstat(path):
        if path == endpoint:
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=runner.os.geteuid(),
                                   st_dev=1, st_ino=2)
        return original_lstat(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(Path, "unlink", lambda path: None)
    def fail_rmdir(path):
        raise OSError("cleanup failure")
    monkeypatch.setattr(Path, "rmdir", fail_rmdir)
    class Child:
        pid = 701
        def poll(self):
            return None
        def wait(self, timeout):
            return 0
        def terminate(self):
            pass
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: Child())
    class Peer:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, value):
            pass
        def connect(self, path):
            pass
        def getsockopt(self, *args):
            return struct.pack("3i", 701, runner.os.geteuid(), 1)
        def sendall(self, payload):
            self.payload = payload
        def recv(self, size):
            if b'"pull"' in self.payload:
                return b'{"state":"OK","events":[{"kind":"BOOK","synthetic_fixture":true}]}\n'
            return b'{"state":"DENIED_READ_ONLY"}\n'
    monkeypatch.setattr(runner.socket, "socket", lambda *args: Peer())
    with pytest.raises(runner.RunnerHold, match="CLEANUP_DEFERRED_UNVERIFIED"):
        runner._fixture_probe(b"fixture source")


def test_owned_socket_waits_for_chmod_before_connect(tmp_path, monkeypatch):
    runner = module()
    ready = []
    monkeypatch.setattr(runner, "_await_ready", lambda process, timeout: ready.append(True))
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    endpoint = private / "fixture.sock"
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(private))
    original_lstat = Path.lstat
    modes = []
    def lstat(path):
        if path == endpoint:
            mode = 0o755 if not modes else 0o600
            modes.append(mode)
            return SimpleNamespace(st_mode=stat.S_IFSOCK | mode, st_uid=runner.os.geteuid(),
                                   st_dev=1, st_ino=2)
        return original_lstat(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    deleted = []
    monkeypatch.setattr(Path, "unlink", lambda path: deleted.append(("unlink", path)))
    monkeypatch.setattr(Path, "rmdir", lambda path: deleted.append(("rmdir", path)))
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    class Child:
        pid = 701
        done = False
        def poll(self):
            return 0 if self.done else None
        def wait(self, timeout):
            self.done = True
            return 0
        def terminate(self):
            self.done = True
    child = Child()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: child)
    class Peer:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, value):
            pass
        def connect(self, path):
            assert ready == [True]
            assert len(modes) >= 2 and modes[-1] == 0o600
        def getsockopt(self, *args):
            return struct.pack("3i", 701, runner.os.geteuid(), 1)
        def sendall(self, payload):
            self.payload = payload
        def recv(self, size):
            if b'"pull"' in self.payload:
                return b'{"state":"OK","events":[{"kind":"BOOK","synthetic_fixture":true}]}\n'
            return b'{"state":"DENIED_READ_ONLY"}\n'
    monkeypatch.setattr(runner.socket, "socket", lambda *args: Peer())
    with pytest.raises(runner.RunnerHold, match="CLEANUP_DEFERRED_UNVERIFIED"):
        runner._fixture_probe(b"fixture source")
    assert deleted == []


@pytest.mark.parametrize("change", ["inode", "uid", "type", "timeout"])
def test_readiness_never_connects_to_changed_or_unready_socket(tmp_path, monkeypatch, change):
    runner = module()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    endpoint = private / "fixture.sock"
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(private))
    original = Path.lstat
    seen = []
    def lstat(path):
        if path == endpoint:
            seen.append(True)
            second = len(seen) > 1
            return SimpleNamespace(
                st_mode=(stat.S_IFREG if change == "type" else stat.S_IFSOCK) | 0o755,
                st_uid=runner.os.geteuid() + (1 if change == "uid" else 0),
                st_dev=1, st_ino=2 + (1 if change == "inode" and second else 0))
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    clock = iter(range(100))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    class Child:
        pid = 701
        stopped = False
        def poll(self):
            return 0 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, timeout):
            return 0
    child = Child()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: child)
    def forbidden(*args):
        raise AssertionError("must not connect before identity and mode are verified")
    monkeypatch.setattr(runner.socket, "socket", forbidden)
    with pytest.raises(runner.RunnerHold):
        runner._fixture_probe(b"fixture source")
    assert child.stopped is True


def test_fixture_signals_only_after_listen(monkeypatch):
    runner = module()
    events = []
    class Server:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def bind(self, path):
            assert current_mask[0] == 0o177
            assert 0o777 & ~current_mask[0] == 0o600
            events.append("bind")
        def listen(self, count):
            events.append("listen")
        def settimeout(self, timeout):
            pass
        def accept(self):
            raise OSError("stop fixture")
    monkeypatch.setattr(runner.socket, "socket", lambda *args: Server())
    current_mask = [0o022]
    def umask(value):
        previous = current_mask[0]
        current_mask[0] = value
        events.append(("umask", value))
        return previous
    monkeypatch.setattr(runner.os, "umask", umask)
    def chmod_forbidden(*args):
        raise AssertionError("must never chmod a replaceable path")
    monkeypatch.setattr(runner.os, "chmod", chmod_forbidden)
    def write(fd, data):
        events.append(("ready", fd, data))
        return 1
    monkeypatch.setattr(runner.os, "write", write)
    with pytest.raises(OSError):
        runner._serve("/synthetic-only")
    assert events == [("umask", 0o177), "bind", ("umask", 0o022),
                      "listen", ("ready", 1, b"R")]
    assert current_mask[0] == 0o022


def test_prebound_socket_does_not_chmod_or_change_umask(monkeypatch):
    runner = module()
    events = []
    current_mask = [0o027]
    def umask(value):
        previous = current_mask[0]
        current_mask[0] = value
        events.append(("umask", value))
        return previous
    class Server:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def bind(self, path):
            events.append("bind-collision")
            raise OSError(errno.EADDRINUSE, "synthetic collision")
    monkeypatch.setattr(runner.socket, "socket", lambda *args: Server())
    monkeypatch.setattr(runner.os, "umask", umask)
    def forbidden(*args):
        raise AssertionError("must not modify a foreign pathname")
    monkeypatch.setattr(runner.os, "chmod", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr(Path, "rmdir", forbidden)
    with pytest.raises(OSError) as error:
        runner._serve("/synthetic-only")
    assert error.value.errno == errno.EADDRINUSE
    assert events == [("umask", 0o177), "bind-collision", ("umask", 0o027)]
    assert current_mask[0] == 0o027


def test_socket_disappearance_after_identity_prevents_connect(tmp_path, monkeypatch):
    runner = module()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    endpoint = private / "fixture.sock"
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(private))
    original = Path.lstat
    seen = []
    def lstat(path):
        if path == endpoint:
            seen.append(True)
            if len(seen) > 1:
                raise FileNotFoundError
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=runner.os.geteuid(),
                                   st_dev=1, st_ino=2)
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    class Child:
        pid = 701
        stopped = False
        def poll(self):
            return 0 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, timeout):
            return 0
    child = Child()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(runner, "_await_ready", lambda process, timeout: None, raising=False)
    def forbidden(*args):
        raise AssertionError("must not connect after socket disappearance")
    monkeypatch.setattr(runner.socket, "socket", forbidden)
    with pytest.raises(runner.RunnerHold, match="SOCKET_UNVERIFIED"):
        runner._fixture_probe(b"fixture source")
    assert child.stopped is True


@pytest.mark.parametrize("message,exited,accepted", [
    (b"", False, False), (b"X", False, False),
    (b"R", True, False), (b"R", False, True),
])
def test_ready_pipe_requires_exact_signal_and_live_child(message, exited, accepted):
    runner = module()
    read_fd, write_fd = runner.os.pipe()
    try:
        if message:
            runner.os.write(write_fd, message)
        class Child:
            stdout = SimpleNamespace(fileno=lambda: read_fd)
            def poll(self):
                return 1 if exited else None
        if accepted:
            runner._await_ready(Child(), 0)
        else:
            with pytest.raises(runner.RunnerHold, match="FIXTURE_NOT_READY"):
                runner._await_ready(Child(), 0)
    finally:
        runner.os.close(read_fd)
        runner.os.close(write_fd)


def test_prebound_foreign_socket_is_never_unlinked_on_child_failure(tmp_path, monkeypatch):
    runner = module()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    endpoint = private / "fixture.sock"
    endpoint.write_bytes(b"foreign socket placeholder")
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(private))
    original = Path.lstat
    def lstat(path):
        info = original(path)
        if path == endpoint:
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=runner.os.geteuid(),
                                   st_dev=info.st_dev, st_ino=info.st_ino)
        return info
    monkeypatch.setattr(Path, "lstat", lstat)
    class Child:
        pid = 701
        stopped = False
        def poll(self):
            return 0 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, timeout):
            return 0
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: Child())
    def not_ready(process, timeout):
        raise runner.RunnerHold("FIXTURE_NOT_READY")
    monkeypatch.setattr(runner, "_await_ready", not_ready)
    def forbidden(*args):
        raise AssertionError("must not connect to a foreign endpoint")
    monkeypatch.setattr(runner.socket, "socket", forbidden)
    with pytest.raises(runner.RunnerHold):
        runner._fixture_probe(b"fixture source")
    assert endpoint.read_bytes() == b"foreign socket placeholder"


def test_real_prebound_socket_is_preserved_on_child_failure(tmp_path, monkeypatch):
    runner = module()
    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EAFNOSUPPORT, errno.ENOSYS}:
            pytest.skip("AF_UNIX unavailable; physical foreign-socket test unverified")
        raise
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    endpoint = private / "fixture.sock"
    with server:
        server.bind(str(endpoint))
        runner.os.chmod(endpoint, 0o600)
        original_inode = endpoint.lstat().st_ino
        monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(private))
        with pytest.raises(runner.RunnerHold):
            runner._fixture_probe(Path(runner.__file__).read_bytes())
        assert endpoint.lstat().st_ino == original_inode
