"""PAPER runtime gateway routing contract, no SDK/network/droplet operations."""

import ast
import inspect
import errno
import os
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch

import bf_production_paper_observer as scanner
import bv_paper_runtime as runtime
import cf_intraday_scalping as scalping
from rc6_ppi_gateway_reader_adapter import local_gateway_owner_custody
from rc6_ppi_shared_feed import ReadOnlyUnixFeedServer, SharedFeedUnavailable


def _calls(function):
    tree = ast.parse(inspect.getsource(function))
    return {node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def test_three_paper_consumers_never_construct_own_sdk_or_read_secrets():
    # A persistent CI guard: do not regress to three independent PPI logins.
    for worker in (runtime.run_reader, scanner.run, scanner._run_command,
                   scalping.run_worker):
        calls = _calls(worker)
        assert "ProductionMarketReader" not in calls
        assert "_secret" not in calls


def test_gateway_rotation_reattaches_scanner_and_scalping_without_429_login():
    disappeared = SharedFeedUnavailable("PPI_GATEWAY_OWNER_CHANGED")
    assert scanner._scanner_gateway_reader_lost(disappeared) is True
    assert scalping._intraday_read_error_code(
        object(), disappeared, lambda error: "PPI_EXCEPTION") == (
            "PPI_GATEWAY_REATTACH_REQUIRED")
    limited = SharedFeedUnavailable("PPI_NATIVE_HTTP_429")
    assert scanner._scanner_gateway_reader_lost(limited) is False
    assert scalping._intraday_read_error_code(
        object(), limited, lambda error: "PPI_HTTP_429") == "PPI_HTTP_429"
    scanner_source = inspect.getsource(scanner.run)
    scalping_source = inspect.getsource(scalping.run_worker)
    exit_source = inspect.getsource(runtime.run_reader)
    assert "_scanner_gateway_reader_lost(exc)" in scanner_source
    assert "GATEWAY_LOCAL_ATTACH_RETRY_SECONDS if local_attach_failure" in scanner_source
    assert "PPI_GATEWAY_REATTACH_REQUIRED" in scalping_source
    assert "next_login = time.monotonic() + 15" in scalping_source
    assert "next_login = time.monotonic() + 15" in exit_source
    assert "gateway_reader_needs_reattach(exc)" in exit_source


def test_local_owner_custody_never_claims_physical_global_exclusivity():
    tick = ReadOnlyUnixFeedServer.process_starttime(os.getpid())
    status = {"state": "OPEN", "owner_pid": os.getpid(),
              "session_generation": "a" * 64,
              "login_invocations_by_owner": 1,
              "guard_login_wire_attempts": 1,
              "guard_http_allowed": 4, "guard_http_blocked": 2,
              "registered_clients": {name: {"pid": os.getpid(),
                "proc_starttime": tick} for name in
                ("scanner", "exit_reader", "scalping")}}
    result = local_gateway_owner_custody(status)
    assert result["state"] == "LOCAL_OWNER_OBSERVED"
    assert result["owner_pid"] == os.getpid()
    assert result["guard_login_wire_attempts"] == 1
    assert result["registered_roles"] == ("exit_reader", "scalping", "scanner")
    # All three role names can be registered against ONE local PID. They are
    # not evidence of three distinct child processes or global custody.
    assert result["three_roles_registered_locally"] is False
    assert result["global_single_session_custody"] == "NO_VERIFICADO"
    assert result["credential_isolation"] == "NO_VERIFICADO"
    assert result["other_ppi_login_processes"] == "NO_VERIFICADO"


def test_runtime_starts_gateway_worker_before_scanner_exit_scalping():
    clock = [100.0]
    started = []

    class Child:
        pid = 100

        def poll(self):
            return None

    children = runtime.ChildProcesses({
        "ppi_gateway": ["gateway"], "scanner": ["scanner"],
        "exit_reader": ["exit"], "intraday_scalping": ["scalping"]},
        startup_grace_seconds=5,
        spawn=lambda command, **kwargs: (started.append(command[0]), Child())[1],
        clock=lambda: clock[0])
    children.poll()
    assert started == ["gateway"]
    clock[0] += 5
    children.poll()
    assert started == ["gateway", "scanner", "exit", "scalping"]


def test_parent_registers_child_pid_birth_and_rebinds_after_owner_generation(tmp_path):
    clock = [100.0]
    generation = ["a" * 64]
    registrations = []

    class Child:
        pid = os.getpid()  # real /proc starttime, no spawned SDK

        def poll(self):
            return None

    class FakeGatewayClient:
        def __init__(self, **kwargs):
            pass

        def owner_status(self):
            return {"state": "OPEN", "session_generation": generation[0]}

        def register_client(self, role, *, pid, proc_starttime):
            registrations.append((role, pid, proc_starttime, generation[0]))

    with patch("rc6_ppi_shared_feed.ReadOnlyUnixFeedClient", FakeGatewayClient), \
         patch("rc6_ppi_gateway_reader_adapter.gateway_socket_path",
               lambda database: tmp_path / "unconnected.sock"), \
         patch.object(runtime, "database_path", lambda: tmp_path / "paper.sqlite"):
        children = runtime.ChildProcesses({
            "ppi_gateway": ["gateway"], "scanner": ["scanner"],
            "exit_reader": ["exit"]}, startup_grace_seconds=5,
            spawn=lambda *a, **kw: Child(), clock=lambda: clock[0])
        children.poll()
        clock[0] += 5
        children.poll()
        assert {r[0] for r in registrations} == {"scanner", "exit_reader"}
        assert all(r[2] == ReadOnlyUnixFeedServer.process_starttime(os.getpid())
                   for r in registrations)
        generation[0] = "b" * 64
        clock[0] += 16
        children.poll()
        assert len(registrations) == 4
        assert {r[3] for r in registrations} == {"a" * 64, "b" * 64}


def test_owner_lifetime_lease_blocks_second_gateway_even_after_cooldown(tmp_path):
    private = tmp_path / "gateway"
    private.mkdir(mode=0o700)
    path = private / "market.sock"
    script = ("from pathlib import Path\n"
              "from bv_paper_runtime import ppi_gateway_lifetime_lease\n"
              "import sys\n"
              "try:\n"
              "    with ppi_gateway_lifetime_lease(Path(sys.argv[1])): pass\n"
              "except RuntimeError as error:\n"
              "    print(str(error))\n"
              "    sys.exit(3)\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(runtime.__file__).resolve().parent)
    def second_gateway():
        return subprocess.run([sys.executable, "-c", script, str(path)],
                              env=env, capture_output=True, text=True, timeout=5)

    with runtime.ppi_gateway_lifetime_lease(path):
        # Independent process can run after any 900s login cooldown. Its
        # lifetime lock is separate and does not expire by wall time.
        blocked = second_gateway()
        assert blocked.returncode == 3
        assert "PPI_GATEWAY_LIFETIME_LEASE_HELD" in blocked.stdout
    successor = second_gateway()
    assert successor.returncode == 0


def test_stale_gateway_socket_reclaimed_only_under_exclusive_owner_lock(tmp_path):
    import pytest
    private = tmp_path / "gateway"
    private.mkdir(mode=0o700)
    path = private / "market.sock"
    try:
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EAFNOSUPPORT, errno.ENOSYS}:
            pytest.skip("SANDBOX_AF_UNIX_UNAVAILABLE")
        raise
    try:
        stale.bind(str(path))
        os.chmod(path, 0o600)
    finally:
        stale.close()  # simulates SIGKILL leaving socket node
    assert path.exists()
    with runtime.ppi_gateway_lifetime_lease(path):
        assert not path.exists()
    live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        live.bind(str(path))
        os.chmod(path, 0o600)
        live.listen(1)
        with pytest.raises(RuntimeError, match="PPI_GATEWAY_SOCKET_LIVE_OWNER"):
            with runtime.ppi_gateway_lifetime_lease(path):
                pass
        assert path.exists()
    finally:
        live.close()
        path.unlink()


def test_owner_lifetime_lease_never_deletes_non_socket_or_symlink(tmp_path):
    import pytest
    private = tmp_path / "gateway"
    private.mkdir(mode=0o700)
    path = private / "market.sock"
    path.write_text("not a socket", encoding="ascii")
    with pytest.raises(RuntimeError, match="PPI_GATEWAY_STALE_SOCKET_UNTRUSTED"):
        with runtime.ppi_gateway_lifetime_lease(path):
            pass
    assert path.read_text(encoding="ascii") == "not a socket"
    path.unlink()
    path.symlink_to(private / "malicious-outside")
    with pytest.raises(RuntimeError, match="PPI_GATEWAY_STALE_SOCKET_UNTRUSTED"):
        with runtime.ppi_gateway_lifetime_lease(path):
            pass
    assert path.is_symlink()
