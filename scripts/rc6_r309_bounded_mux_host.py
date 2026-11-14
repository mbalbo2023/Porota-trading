"""R309 bounded trusted-main SSH transport with separate file observation.

This adapter does not fetch or grant ROOT authority. R305 owns its command
grammar, its independent witness and each transaction's fresh owner GO.
Constructing this class never opens a connection; one context opens one SSH
master. Child calls cannot fall back to another TCP connection.
"""

from importlib import import_module
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time

from scripts import rc6_host_rootowned_installer_contract as r280
from scripts import rc6_r302_bootstrap_receipt_preflight as r302
from scripts.rc6_r295_host_bootstrap_guard import (
    BootstrapHold, HOST_ID_COMMAND, HOST_REGION_COMMAND, _strict_json, run_bounded_ssh_command,
)


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z")
_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,30}\Z")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,70}\Z")
_CAPS = frozenset((0, 32, 128, 256))
# R280 emits a 261-byte success receipt; reject smaller caps before mutation.
# Only the exact reviewed installed-CLI verb may use 512 bytes.
_INSTALL_RECEIPT_CAP = 512
_INSTALL_APPROVED_COMMAND = (
    "/usr/bin/sudo -n /usr/bin/python3 -I "
    "/usr/local/sbin/porota-rc6-host-installer install-approved"
)
_MAX_STDIN = 8192
_MAX_CALL_SECONDS = 15
_TOTAL_SECONDS = 540
_FILE_PROBE_SECONDS = 180
_R305_MODULE = "scripts.rc6_r305_root_pin_transition_guard"
PROBE_OPERATION = "PROBE_FILES_ONLY"
PROBE_UID_COMMAND = "/usr/bin/id -u"
PROBE_PYTHON_VERSION_COMMAND = "/usr/bin/python3 -I --version"
PROBE_GENERATIONS = "/var/lib/porota-rc6-oci-custody/generations"
_SUDO = "/usr/bin/sudo -n /usr/bin/"
CUSTODIAN_TARGET = "/usr/local/libexec/porota-rc6-custody/rc6-genesis-custodian"
CUSTODIAN_DIRS = ("/", "/usr", "/usr/local", "/usr/local/libexec", "/usr/local/libexec/porota-rc6-custody")
_CUSTODIAN_VERBS = {"PREPARE_ENV": "prepare-env", "APPLY_GENESIS": "apply"}


def probe_stat_command(path):
    return f"{_SUDO}stat -c '%u|%g|%a|%h|%F|%d|%i|%s' -- {path}"


def probe_test_command(test, path):
    return f"{_SUDO}test {test} {path}"


def probe_hash_command(path):
    return f"{_SUDO}sha256sum -- {path}"


def _file_probe_commands():
    """Read-only filesystem observations and the version of the custodian's interpreter."""
    from scripts.rc6_genesis_launcher_authority import LAUNCHER_TARGET

    bootstrap = tuple(path for path, _, _ in r302.ROOT_FILES)
    targets = tuple(row[0] for row in r280.TARGETS.values()) + (PROBE_GENERATIONS, LAUNCHER_TARGET, CUSTODIAN_TARGET)
    paths = (*r302.ROOT_DIRECTORIES, *bootstrap, *targets)
    hashes = (*bootstrap, r280.TARGETS['helper'][0], r280.TARGETS['custody'][0], LAUNCHER_TARGET, CUSTODIAN_TARGET)
    return frozenset((
        HOST_ID_COMMAND, HOST_REGION_COMMAND, PROBE_UID_COMMAND, PROBE_PYTHON_VERSION_COMMAND,
        *(probe_stat_command(path) for path in paths),
        *(probe_test_command('! -L', path) for path in paths),
        *(probe_test_command('-x', path) for path in CUSTODIAN_DIRS),
        *(probe_test_command('! -e', path) for path in targets),
        *(probe_test_command('-e', path) for path in targets),
        *(probe_hash_command(path) for path in hashes),
    ))


class R309Hold(RuntimeError):
    """Stable non-secret HOLD79; never include SSH output or credentials."""

    exit_code = 79

    def __init__(self, reason):
        value = reason if type(reason) is str and _REASON.fullmatch(reason) else "UNVERIFIED"
        self.reason = value
        super().__init__("RC6_R309_SSH_HOLD79:" + value)


def _require(ok, reason):
    if not ok:
        raise R309Hold(reason)


def _trusted_r305_commands():
    """Require the reviewed trusted-main R305 module, not workflow input."""
    try:
        r305 = import_module(_R305_MODULE)
        commands = r305.reviewed_root_commands()
        identity = (r305.HOST_ID_COMMAND, r305.HOST_REGION_COMMAND)
    except (ImportError, AttributeError, TypeError):
        raise R309Hold("TRUSTED_R305_MODULE_MISSING") from None
    _require(type(commands) is frozenset and 0 < len(commands) <= 512 and
             all(type(item) is str and 0 < len(item) <= 512 and
                 item.startswith("/usr/bin/sudo -n /usr/bin/")
                 for item in commands) and
             type(identity) is tuple and len(identity) == 2 and
             all(type(item) is str and item.startswith(
                 "/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
                 "http://169.254.169.254/metadata/v1/")
                 for item in identity),
             "TRUSTED_R305_ALLOWLIST_INVALID")
    return commands | frozenset(identity)


def _validated_secrets(env):
    _require(type(env) is dict or hasattr(env, "get"), "TRUSTED_ENV_REQUIRED")
    _require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
             env.get("GITHUB_REPOSITORY") == "mbalbo2023/Porota-trading" and
             env.get("GITHUB_REF") == "refs/heads/main" and
             env.get("GITHUB_ACTOR") == "mbalbo2023" and
             env.get("GITHUB_TRIGGERING_ACTOR") == "mbalbo2023" and
             type(env.get("GITHUB_SHA")) is str and
             _SHA.fullmatch(env["GITHUB_SHA"]),
             "TRUSTED_MAIN_DISPATCH_REQUIRED")
    host, user = env.get("DO_HOST"), env.get("DO_USER")
    _require(type(host) is str and _HOST.fullmatch(host) and
             type(user) is str and _USER.fullmatch(user),
             "SSH_SELECTOR_INVALID")
    key, known = env.get("DO_SSH_KEY"), env.get("DO_KNOWN_HOSTS")
    _require(type(key) is str and 32 <= len(key) <= 16384 and
             "PRIVATE KEY" in key and "\x00" not in key and
             type(known) is str and 16 <= len(known) <= 8192 and
             "\x00" not in known,
             "SSH_PROTECTED_SECRETS_REQUIRED")
    return host, user, (key.rstrip("\n") + "\n").encode("utf-8"), (
        known.rstrip("\n") + "\n").encode("utf-8")


def _write_secret(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        position = 0
        while position < len(data):
            written = os.write(fd, data[position:])
            _require(written > 0, "SSH_SECRET_WRITE_SHORT")
            position += written
        os.fsync(fd)
    finally:
        os.close(fd)


class R309BoundedMuxHost:
    """One strict SSH master, R305's literal allowlist, bounded child calls."""

    def __init__(self, env, *, operation="R305"):
        _require(operation in ("R305", PROBE_OPERATION), "SSH_OPERATION_UNKNOWN")
        self.host, self.user, self.key_bytes, self.known_bytes = _validated_secrets(env)
        self._file_probe = operation == PROBE_OPERATION
        self._total_seconds = _FILE_PROBE_SECONDS if self._file_probe else _TOTAL_SECONDS
        self.commands = _file_probe_commands() if self._file_probe else _trusted_r305_commands()
        self.temp = None
        self.control_socket = None
        self.ssh_args = None
        self.key_path = None
        self.known_hosts_path = None
        self.master = None
        self.transaction_deadline = None
        self.command_count = 0
        self.poisoned = False

    def _verified_master_socket(self):
        if self.control_socket is None:
            return False
        try:
            info = self.control_socket.lstat()
        except FileNotFoundError:
            return False
        return (stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid() and
                not info.st_mode & 0o022)

    def __enter__(self):
        _require(self.temp is None and self.master is None, "SSH_CONTEXT_REENTRY")
        try:
            self.transaction_deadline = time.monotonic() + self._total_seconds
            self.temp = tempfile.TemporaryDirectory(prefix="rc6-r309-ssh-")
            folder = Path(self.temp.name)
            self.key_path = folder / "id_ed25519"
            self.known_hosts_path = folder / "known_hosts"
            _write_secret(self.key_path, self.key_bytes)
            _write_secret(self.known_hosts_path, self.known_bytes)
            self.control_socket = folder / "ssh-control.sock"
            self.ssh_args = [
                "/usr/bin/ssh", "-T", "-F", "/dev/null",
                "-i", str(self.key_path), "-S", str(self.control_socket),
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "UserKnownHostsFile=" + str(self.known_hosts_path),
                "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", "ProxyCommand=/usr/bin/false",
                "-o", "ProxyJump=none", "-o", "ControlMaster=auto",
                "-o", "ClearAllForwardings=yes", "-o", "UpdateHostKeys=no",
                "-o", "PasswordAuthentication=no",
                "-o", "KbdInteractiveAuthentication=no",
                "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
                "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
                "-o", "LogLevel=ERROR", f"{self.user}@{self.host}",
            ]
            master_args = [
                "ProxyCommand=none" if part == "ProxyCommand=/usr/bin/false" else part
                for part in self.ssh_args
            ]
            self.master = subprocess.Popen(
                master_args[:-1] + ["-M", "-N", master_args[-1]],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
            )
            deadline = min(self.transaction_deadline, time.monotonic() + 8)
            while time.monotonic() < deadline:
                _require(self.master.poll() is None, "SSH_MASTER_EXITED")
                if self._verified_master_socket():
                    return self
                time.sleep(0.05)
            raise R309Hold("SSH_MASTER_SOCKET_TIMEOUT")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def call(self, command, *, stdin=None, max_output_bytes=256, timeout_s=15):
        _require(not self.poisoned, "SSH_TRANSPORT_POISONED")
        _require(type(command) is str and command in self.commands,
                 "SSH_COMMAND_NOT_REVIEWED")
        _require(not self._file_probe or stdin is None, "SSH_FILE_PROBE_STDIN_FORBIDDEN")
        _require(stdin is None or type(stdin) is bytes and
                 0 < len(stdin) <= _MAX_STDIN,
                 "SSH_STDIN_INVALID")
        _require(type(max_output_bytes) is int and
                 (max_output_bytes == _INSTALL_RECEIPT_CAP
                  if command == _INSTALL_APPROVED_COMMAND else
                  max_output_bytes in _CAPS),
                 "SSH_OUTPUT_CAP_INVALID")
        _require(type(timeout_s) in (int, float) and
                 math.isfinite(timeout_s) and
                 0 < timeout_s <= _MAX_CALL_SECONDS,
                 "SSH_CALL_TIMEOUT_INVALID")
        return self._run_child(command, stdin, max_output_bytes, timeout_s)

    def _run_child(self, command, stdin, max_output_bytes, timeout_s):
        _require(not self.poisoned, "SSH_TRANSPORT_POISONED")
        _require(self.ssh_args is not None and self.master is not None and
                 self.master.poll() is None and self._verified_master_socket(),
                 "SSH_MASTER_UNAVAILABLE")
        remaining = (self.transaction_deadline - time.monotonic()
                     if self.transaction_deadline is not None else 0)
        _require(remaining > 0.01, "SSH_TOTAL_DEADLINE_EXCEEDED")
        self.command_count += 1
        try:
            result = run_bounded_ssh_command(
                self.ssh_args + [command], b"" if stdin is None else stdin,
                timeout_s=min(timeout_s, remaining),
                output_cap=max_output_bytes,
            )
        except Exception:
            # Never echo stderr, exception arguments, SSH key or host selector.
            self.poisoned = True
            raise R309Hold("SSH_COMMAND_TRANSPORT_UNVERIFIED") from None
        if not (type(result) is tuple and len(result) == 2 and
                type(result[0]) is int and 0 <= result[0] < 255 and
                type(result[1]) is bytes and
                len(result[1]) <= max_output_bytes):
            self.poisoned = True
            raise R309Hold("SSH_COMMAND_RESULT_UNVERIFIED")
        return result

    def __exit__(self, *_exc):
        try:
            if self.master is not None:
                if self.master.poll() is None:
                    self.master.terminate()
                    try:
                        self.master.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.master.kill()
                        self.master.wait(timeout=3)
                self.master = None
            if self.temp is not None:
                self.temp.cleanup()
        finally:
            self.temp = None
            self.control_socket = None
            self.ssh_args = None
            self.transaction_deadline = None
            self.key_bytes = b""
            self.known_bytes = b""


class R367CustodianTransport(R309BoundedMuxHost):
    """One verified mux and a fixed zipapp verb. Input is data, never executable stdin.

    Both verbs accept at most 1 MiB, including the signed claims and the public
    release sources used by POST_D_E. The custodian authenticates GitHub OIDC
    independently. R321 checks the lease and validates receipts before export.
    """

    def __init__(self, env, *, operation, custodian_sha256, host_identity_sha256):
        from scripts import rc6_genesis_launcher_authority as authority

        _require(type(operation) is str and operation in _CUSTODIAN_VERBS, "CUSTODIAN_OPERATION")
        _require(type(custodian_sha256) is str and re.fullmatch("[0-9a-f]{64}", custodian_sha256),
                 "CUSTODIAN_PACKAGE_PIN")
        try:
            r302.validate_environment(env)
            commitment = authority.host_identity_sha256(env)
        except (r302.R302Hold, BootstrapHold):
            raise R309Hold("CUSTODIAN_HOST_PINS_UNVERIFIED") from None
        _require(type(host_identity_sha256) is str and commitment == host_identity_sha256,
                 "CUSTODIAN_HOST_COMMITMENT_MISMATCH")
        super().__init__(env, operation=PROBE_OPERATION)
        self.commands = frozenset()
        self._total_seconds = _TOTAL_SECONDS
        self._expected_identity = {k: env[k] for k in ("RC6_EXPECTED_DO_ID", "RC6_EXPECTED_DO_REGION")}
        self._operation = operation
        self._package_sha = custodian_sha256

    def call(self, *args, **kwargs):
        raise R309Hold("CUSTODIAN_FIXED_VERB_REQUIRED")

    def _read(self, command, cap):
        status, raw = self._run_child(command, None, cap, _MAX_CALL_SECONDS)
        _require(status == 0, "CUSTODIAN_PHYSICAL_READ_FAILED")
        return raw

    def _snapshot(self):
        snapshot = []
        for path in (*CUSTODIAN_DIRS, CUSTODIAN_TARGET):
            directory = path != CUSTODIAN_TARGET
            self._read(probe_test_command('! -L', path), 0)
            raw = self._read(probe_stat_command(path), 128)
            try:
                _require(re.fullmatch(rb'[ -~]{1,127}\n', raw), 'CUSTODIAN_PHYSICAL_STAT')
                row = raw[:-1].decode('ascii')
                r302._expected_stat(path, f'STAT|{path}|{row}', directory=directory,
                                    mode=None if directory else 0o555)
                if not directory:
                    _require(0 < int(row.rsplit('|', 1)[1]) <= 1024**2, "CUSTODIAN_PACKAGE_SIZE")
            except (ValueError, r302.R302Hold):
                raise R309Hold("CUSTODIAN_PHYSICAL_STAT") from None
            snapshot.append(raw)
        return snapshot

    @staticmethod
    def _validate_request(request_bytes):
        try:
            document = _strict_json(request_bytes, limit=1024**2)
        except BootstrapHold:
            raise R309Hold("CUSTODIAN_REQUEST_INVALID") from None
        _require(type(document) is dict and document, "CUSTODIAN_REQUEST_INVALID")

    def invoke(self, request_bytes, *, recheck_authority):
        """A zero-argument factory mints its short-lived OIDC after the final recheck.

        Bytes supplied directly are validated before any physical reads. The
        bounded private response is returned only to the validating caller.
        """
        _require(callable(recheck_authority), "CUSTODIAN_AUTHORITY_REQUIRED")
        factory = request_bytes if callable(request_bytes) else None
        if factory is None:
            self._validate_request(request_bytes)
        try:
            observations = [self._read(command, 32).removesuffix(b'\n')
                            for command in (HOST_ID_COMMAND, HOST_REGION_COMMAND, PROBE_UID_COMMAND)]
            r302.verify_host_identity(b'\n'.join(observations) + b'\n', self._expected_identity)
        except r302.R302Hold:
            raise R309Hold("CUSTODIAN_PHYSICAL_IDENTITY") from None
        before = self._snapshot()
        digest = self._read(probe_hash_command(CUSTODIAN_TARGET), 256)
        _require(digest == f'{self._package_sha}  {CUSTODIAN_TARGET}\n'.encode('ascii'),
                 "CUSTODIAN_PACKAGE_DIGEST")
        _require(self._snapshot() == before, "CUSTODIAN_PHYSICAL_CHANGED")
        try:
            authorized = recheck_authority()
        except Exception:
            raise R309Hold("CUSTODIAN_AUTHORITY_UNVERIFIED") from None
        _require(authorized is True, "CUSTODIAN_AUTHORITY_UNVERIFIED")
        command = "/usr/bin/sudo -n " + CUSTODIAN_TARGET + " " + _CUSTODIAN_VERBS[self._operation]
        if factory is not None:
            try:
                request_bytes = factory()
            except Exception:
                raise R309Hold("CUSTODIAN_REQUEST_UNAVAILABLE") from None
            self._validate_request(request_bytes)
        status, receipt = self._run_child(command, request_bytes, 4096, 60)
        if status != 0:
            self.poisoned = True
            raise R309Hold("CUSTODIAN_VERB_HOLD")
        try:
            result = _strict_json(receipt, limit=4096)
        except BootstrapHold:
            self.poisoned = True
            raise R309Hold("CUSTODIAN_RECEIPT_INVALID") from None
        if type(result) is not dict or not result:
            self.poisoned = True
            raise R309Hold("CUSTODIAN_RECEIPT_INVALID")
        return receipt
