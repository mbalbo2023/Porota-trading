"""Synthetic-only H5 fixture controller; not a QA or release certificate.

Only a private child created from this reviewed file may receive the synthetic
order request. No candidate code, broker, Docker or external endpoint is used.
The calling workflow and hosted runner remain trust assumptions.
"""
from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import select
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time


class RunnerHold(ValueError):
    """Synthetic probe could not be performed safely."""


_EVENT = {"kind": "BOOK", "synthetic_fixture": True}
_PULL = b'{"op":"pull","consumer":"watch","after_sequence":0}\n'
_ORDER = b'{"op":"place_order","consumer":"watch"}\n'
# Linux fcntl UAPI values; some supported Python builds omit these names.
_F_ADD_SEALS, _F_GET_SEALS = 1033, 1034
_ALL_SEALS = 0x0001 | 0x0002 | 0x0004 | 0x0008
_FILES = ("scripts/rc6_ci_ws12_h5_runner.py", "scripts/rc6_ci_ws12_h5_oracle.py",
          "scripts/rc6_ci_ws12_h5_custodian.py", "scripts/rc6_ci_ws12_oci_supervisor.py",
          "scripts/rc6_ci_ws12_trusted_receipt.py", "scripts/rc6_ci_ws12_main_entry.py")


def _send_checked(conn, *, fixture_pid, fixture_uid, packet):
    """Never send before kernel credentials identify the exact owned child."""
    if (type(fixture_pid) is not int or fixture_pid <= 0 or
            type(fixture_uid) is not int or fixture_uid < 0 or packet not in (_PULL, _ORDER)):
        raise RunnerHold("PEER_UNVERIFIED")
    try:
        raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        pid, uid, _ = struct.unpack("3i", raw)
        if (pid, uid) != (fixture_pid, fixture_uid):
            raise RunnerHold("PEER_UNVERIFIED")
        conn.sendall(packet)
    except (OSError, struct.error, AttributeError):
        raise RunnerHold("PEER_UNVERIFIED") from None


def _checkout_matches(sha):
    root = Path(__file__).resolve().parent.parent
    try:
        def git(*args):
            return subprocess.run(["git", "-C", str(root), *args],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=5, check=True,
                                  env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}).stdout
        if git("rev-parse", "HEAD").strip().decode("ascii") != sha:
            return False
        source = None
        for name in _FILES:
            path = root / name
            reviewed = git("show", sha + ":" + name)
            if path.is_symlink() or path.read_bytes() != reviewed:
                return False
            if name == _FILES[0]:
                source = reviewed
        return source
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return False


def _serve(path):
    """The child serves fixed in-memory data, never imports candidate code."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        old_mask = os.umask(0o177)
        try:
            server.bind(path)
        finally:
            os.umask(old_mask)
        server.listen(2)
        if os.write(1, b"R") != 1:
            return 79
        server.settimeout(5)
        for expected, response in ((_PULL, {"state": "OK", "events": [_EVENT]}),
                                   (_ORDER, {"state": "DENIED_READ_ONLY"})):
            conn, _ = server.accept()
            with conn:
                conn.settimeout(2)
                data = bytearray()
                while not data.endswith(b"\n") and len(data) <= 4096:
                    part = conn.recv(4097 - len(data))
                    if not part:
                        return 79
                    data.extend(part)
                if bytes(data) != expected:
                    return 79
                conn.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")
    return 0


def _await_ready(process, timeout):
    """Read exactly one child-owned signal after private bind and listen."""
    fd = process.stdout.fileno()
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready or os.read(fd, 1) != b"R" or process.poll() is not None:
        raise RunnerHold("FIXTURE_NOT_READY")


def _fixture_probe(source):
    from scripts.rc6_ci_ws12_h5_oracle import assess, parse_response
    directory = Path(tempfile.mkdtemp(prefix="rc6-h5-synthetic-"))
    endpoint = directory / "fixture.sock"
    process = None
    code_fd = None
    deadline = time.monotonic() + 5
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise RunnerHold("TIMEOUT")
        return value
    try:
        if stat.S_IMODE(directory.lstat().st_mode) != 0o700:
            raise RunnerHold("DIRECTORY_UNVERIFIED")
        if type(source) is not bytes or not 1 <= len(source) <= 256 * 1024:
            raise RunnerHold("SOURCE_UNVERIFIED")
        code_fd = os.memfd_create("rc6-h5-fixture", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
        pending = memoryview(source)
        while pending:
            count = os.write(code_fd, pending)
            if count <= 0:
                raise RunnerHold("SOURCE_UNVERIFIED")
            pending = pending[count:]
        fcntl.fcntl(code_fd, _F_ADD_SEALS, _ALL_SEALS)
        if fcntl.fcntl(code_fd, _F_GET_SEALS) & _ALL_SEALS != _ALL_SEALS:
            raise RunnerHold("SOURCE_UNVERIFIED")
        process = subprocess.Popen([sys.executable, "-I", "-S", f"/proc/self/fd/{code_fd}",
                                    "--synthetic-fixture", str(endpoint)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, close_fds=True,
                                   pass_fds=(code_fd,), env={"PATH": "/usr/bin:/bin"})
        observed_identity = None
        while True:
            if process.poll() is not None:
                raise RunnerHold("FIXTURE_EXIT")
            try:
                info = endpoint.lstat()
            except FileNotFoundError:
                if observed_identity is not None:
                    raise RunnerHold("SOCKET_UNVERIFIED") from None
                time.sleep(min(0.02, remaining()))
                continue
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                raise RunnerHold("SOCKET_UNVERIFIED")
            current_identity = (info.st_dev, info.st_ino)
            if observed_identity is not None and current_identity != observed_identity:
                raise RunnerHold("SOCKET_UNVERIFIED")
            observed_identity = current_identity
            if stat.S_IMODE(info.st_mode) == 0o600:
                break
            time.sleep(min(0.02, remaining()))
        _await_ready(process, remaining())
        try:
            info = endpoint.lstat()
        except FileNotFoundError:
            raise RunnerHold("SOCKET_UNVERIFIED") from None
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or (info.st_dev, info.st_ino) != observed_identity):
            raise RunnerHold("SOCKET_UNVERIFIED")
        def request(packet):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(remaining())
                client.connect(str(endpoint))
                client.settimeout(remaining())
                _send_checked(client, fixture_pid=process.pid, fixture_uid=os.geteuid(), packet=packet)
                try:
                    current = endpoint.lstat()
                except FileNotFoundError:
                    raise RunnerHold("SOCKET_UNVERIFIED") from None
                if (not stat.S_ISSOCK(current.st_mode) or current.st_uid != os.geteuid()
                        or stat.S_IMODE(current.st_mode) != 0o600
                        or (current.st_dev, current.st_ino) != observed_identity):
                    raise RunnerHold("SOCKET_UNVERIFIED")
                data = bytearray()
                while not data.endswith(b"\n") and len(data) <= 131073:
                    client.settimeout(remaining())
                    chunk = client.recv(min(4096, 131074 - len(data)))
                    remaining()
                    if not chunk:
                        break
                    data.extend(chunk)
                return parse_response(bytes(data))
        report = assess(request(_PULL), request(_ORDER), expected_event=_EVENT)
        if process.wait(timeout=remaining()) != 0 or report.get("protocol_probe_pass") is not True:
            raise RunnerHold("FIXTURE_FAILED")
        return True
    finally:
        if code_fd is not None:
            os.close(code_fd)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process is not None and getattr(process, "stdout", None) is not None:
            process.stdout.close()
        # Same-UID processes can replace a pathname between lstat and unlink.
        # Preserve every filesystem entry until an independent isolation or
        # atomic ownership mechanism is available; never report cleanup green.
        if sys.exc_info()[0] is None:
            raise RunnerHold("CLEANUP_DEFERRED_UNVERIFIED")


def run(context):
    from scripts.rc6_ci_ws12_h5_custodian import Custody
    from scripts.rc6_ci_ws12_main_entry import context_is_main_dispatch
    result = Custody().receipt()
    result["synthetic_fixture_observed"] = False
    result["cleanup_status"] = "NOT_ATTEMPTED"
    try:
        if not context_is_main_dispatch(context):
            return result
        source = _checkout_matches(context["GITHUB_SHA"])
        if type(source) is not bytes or not source:
            return result
        result["cleanup_status"] = "DEFERRED_UNVERIFIED"
        result["synthetic_fixture_observed"] = _fixture_probe(source)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        pass
    return result


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--synthetic-fixture":
        try:
            raise SystemExit(_serve(sys.argv[2]))
        except (OSError, ValueError):
            raise SystemExit(79) from None
    # No unverified standalone CLI entry; workflow integration is not in scope.
    raise SystemExit(79)
