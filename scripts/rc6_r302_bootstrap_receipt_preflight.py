#!/usr/bin/env python3
"""Independent, read-only R302 physical witness of R301 files-only bootstrap.

This is deliberately NOT an installer, HOST GO, quiescence verifier, root
bundle approval, or Deploy V2 permission. It never executes installed helpers,
Docker, systemd or a root shell. Only a fixed set of sudo stat/sha256sum/test
commands runs remotely after verifying protected DigitalOcean identity pins.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time


# Immutable R301 OWNER GO evidence, independently reviewed on trusted main.
# The D-release manifest hash is intentionally NOT a 5-role root-bundle hash;
# witnessing the installed historical pin cannot make root_bundle_ready=YES.
BOOTSTRAP_MAIN_SHA = "090246d114c2f570bf40b1eace0ac301030a3a9c"
BOOTSTRAP_OWNER_GO_COMMENT_ID = 6840227781
APPROVED_R301_D_MANIFEST_SHA256 = (
    "ba6da9439da6881c4f30708739357cdfc6da1da40e4ead74749119b27cdc9c97"
)
TRUSTED_INSTALLER_CLI_SHA256 = (
    "0d46b0f99ce09fdf4d851255e79fba4fe8af98fd7ab62c98f3c1938c484f8c7a"
)
TRUSTED_INSTALLER_CONTRACT_SHA256 = (
    "8ef4f5892560e496a85cae1df159e45b843d9f6047c3d87f68bc0b369f61adc9"
)

ROOT_LOCK = "/var/lib/.porota-rc6-r297-bootstrap-reserved"
ROOT_CLI = "/usr/local/sbin/porota-rc6-host-installer"
ROOT_CONTRACT = (
    "/usr/local/libexec/porota-rc6-custody/"
    "rc6_host_rootowned_installer_contract.py"
)
ROOT_PIN = "/var/lib/porota-rc6-oci-custody/installer-approved-manifest.sha256"
ROOT_RECEIPT = "/var/lib/porota-rc6-oci-custody/bootstrap-r297-complete.json"
ROOT_DIRECTORIES = (
    "/", "/usr", "/usr/local", "/usr/local/sbin", "/var", "/var/lib",
    "/usr/local/libexec", "/usr/local/libexec/porota-rc6-custody",
    "/var/lib/porota-rc6-oci-custody", ROOT_LOCK,
)
ROOT_FILES = (
    (ROOT_CLI, 0o555, TRUSTED_INSTALLER_CLI_SHA256),
    (ROOT_CONTRACT, 0o400, TRUSTED_INSTALLER_CONTRACT_SHA256),
    (ROOT_PIN, 0o400, None),
    (ROOT_RECEIPT, 0o400, None),
)
ROOT_STAGES = (
    "/usr/local/sbin/.porota-rc6-host-installer.r297-stage",
    "/usr/local/libexec/porota-rc6-custody/.root-installer-contract.r297-stage",
    "/var/lib/porota-rc6-oci-custody/.approved-manifest-pin.r297-stage",
    "/var/lib/porota-rc6-oci-custody/.bootstrap-r297-complete.stage",
)
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z")
_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,30}\Z")
_ID = re.compile(r"[1-9][0-9]{5,19}\Z")
_REGION = re.compile(r"[a-z]{2,12}[0-9]{1,2}\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_WITNESS_BYTES = 16384


class R302Hold(RuntimeError):
    exit_code = 79

    def __init__(self, reason):
        self.reason = reason if re.fullmatch(r"[A-Z][A-Z0-9_]{0,70}", reason) else "UNVERIFIED"
        super().__init__(self.reason)


def _require(ok, reason):
    if not ok:
        raise R302Hold(reason)


def _expected_receipt_bytes():
    doc = {
        "schema": "RC6_R297_ROOT_BOOTSTRAP_FILES_ONLY_V1",
        "status": "INSTALLED_FILES_ONLY_NOT_HOST_GO",
        "trusted_main_sha": BOOTSTRAP_MAIN_SHA,
        "owner_go_comment_id": BOOTSTRAP_OWNER_GO_COMMENT_ID,
        "approved_manifest_sha256": APPROVED_R301_D_MANIFEST_SHA256,
        "installer_cli_sha256": TRUSTED_INSTALLER_CLI_SHA256,
        "installer_contract_sha256": TRUSTED_INSTALLER_CONTRACT_SHA256,
        "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "host_control_verified": False,
    }
    return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def _expected_hashes():
    return {
        ROOT_CLI: TRUSTED_INSTALLER_CLI_SHA256,
        ROOT_CONTRACT: TRUSTED_INSTALLER_CONTRACT_SHA256,
        ROOT_PIN: hashlib.sha256(
            (APPROVED_R301_D_MANIFEST_SHA256 + "\n").encode("ascii")
        ).hexdigest(),
        ROOT_RECEIPT: hashlib.sha256(_expected_receipt_bytes()).hexdigest(),
    }


def validate_environment(env):
    """Reject untrusted dispatch/SSH/DO inputs before opening any connection."""
    _require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
             env.get("GITHUB_REPOSITORY") == "mbalbo2023/Porota-trading" and
             env.get("GITHUB_REF") == "refs/heads/main" and
             env.get("GITHUB_ACTOR") == "mbalbo2023" and
             type(env.get("GITHUB_SHA")) is str and
             _HEX40.fullmatch(env["GITHUB_SHA"]), "TRUSTED_MAIN_DISPATCH_REQUIRED")
    _require(type(env.get("RC6_EXPECTED_DO_ID")) is str and
             _ID.fullmatch(env["RC6_EXPECTED_DO_ID"]) and
             type(env.get("RC6_EXPECTED_DO_REGION")) is str and
             _REGION.fullmatch(env["RC6_EXPECTED_DO_REGION"]),
             "PROTECTED_DO_PINS_UNVERIFIED")
    _require(type(env.get("DO_HOST")) is str and
             _HOST.fullmatch(env["DO_HOST"]) and
             type(env.get("DO_USER")) is str and
             _USER.fullmatch(env["DO_USER"]) and
             env["DO_USER"] == "porotaadmin", "SSH_DESTINATION_UNVERIFIED")
    for key in ("DO_SSH_KEY", "DO_KNOWN_HOSTS"):
        val = env.get(key)
        _require(type(val) is str and 0 < len(val) <= 32768 and
                 "\x00" not in val and "\r" not in val,
                 "SSH_PROTECTED_CREDENTIAL_UNVERIFIED")


def verify_host_identity(raw, env):
    """DO metadata is compared privately; do not print or hash its low entropy."""
    _require(type(raw) is bytes and len(raw) <= 96 and
             raw == (env["RC6_EXPECTED_DO_ID"] + "\n" +
                     env["RC6_EXPECTED_DO_REGION"] + "\n1000\n").encode("ascii"),
             "DO_IDENTITY_UNVERIFIED")


def _expected_stat(path, actual, *, directory=False, mode=None, size=None):
    try:
        prefix, name, uid, gid, mode_raw, links_raw, kind, dev_raw, ino_raw, size_raw = (
            actual.split("|"))
        bits = int(mode_raw, 8)
        links, dev, ino, length = map(int, (links_raw, dev_raw, ino_raw, size_raw))
    except (TypeError, ValueError) as exc:
        raise R302Hold("INODE_STAT_INVALID") from exc
    _require(prefix == "STAT" and name == path and uid == "0" and gid == "0" and
             re.fullmatch(r"[0-7]{3,4}", mode_raw) and dev > 0 and ino > 0 and
             length >= 0 and links >= 1 and not bits & 0o022,
             "ROOT_OWNER_OR_MODE_UNVERIFIED")
    _require(kind == ("directory" if directory else "regular file") and
             (directory or links == 1) and (mode is None or bits == mode) and
             (size is None or length == size),
             "ROOT_KIND_MODE_LINKS_OR_SIZE_UNVERIFIED")
    return (dev, ino)


def verify_witness(raw):
    """Parse a single strict physical observation, never metadata from a bundle."""
    _require(type(raw) is bytes and 0 < len(raw) <= _MAX_WITNESS_BYTES and
             raw.endswith(b"\n") and b"\r" not in raw and b"\x00" not in raw,
             "WITNESS_SIZE_OR_ENCODING_UNVERIFIED")
    try:
        lines = raw.decode("ascii", "strict").split("\n")[:-1]
    except UnicodeError as exc:
        raise R302Hold("WITNESS_ENCODING_UNVERIFIED") from exc
    n_dirs, n_files = len(ROOT_DIRECTORIES), len(ROOT_FILES)
    _require(len(lines) == n_dirs + 3 * n_files + 1 and
             lines[-1] == "STAGES|ABSENT", "WITNESS_SHAPE_OR_STAGES_UNVERIFIED")
    i = 0
    for directory in ROOT_DIRECTORIES:
        _expected_stat(directory, lines[i], directory=True,
                       mode=0o700 if directory == ROOT_LOCK else None)
        i += 1
    first_stats = {}
    for path, mode, _ in ROOT_FILES:
        size = (65 if path == ROOT_PIN else len(_expected_receipt_bytes())
                if path == ROOT_RECEIPT else None)
        first_stats[path] = lines[i]
        _expected_stat(path, lines[i], mode=mode, size=size)
        i += 1
    expected_hashes = _expected_hashes()
    for path, _mode, _ in ROOT_FILES:
        expected = f"HASH|{path}|{expected_hashes[path]}  {path}"
        reason = ("MANIFEST_PIN_HASH_MISMATCH" if path == ROOT_PIN else
                  "BOOTSTRAP_RECEIPT_HASH_MISMATCH" if path == ROOT_RECEIPT else
                  "REVIEWED_ROOT_FILE_HASH_MISMATCH")
        _require(lines[i] == expected, reason)
        i += 1
    for path, _mode, _ in ROOT_FILES:
        _require(lines[i] == first_stats[path], "INODE_CHANGED")
        i += 1
    return "FILES_WITNESSED_ONLY_NOT_HOST_GO"


def _identity_script():
    return ("set -Eeuo pipefail\n"
            "id=$(/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
            "http://169.254.169.254/metadata/v1/id)\n"
            "region=$(/usr/bin/curl -fsS --connect-timeout 3 --max-time 5 "
            "http://169.254.169.254/metadata/v1/region)\n"
            "uid=$(/usr/bin/id -u)\n"
            "/usr/bin/printf '%s\\n%s\\n%s\\n' \"$id\" \"$region\" \"$uid\"\n").encode("ascii")


def _witness_script():
    """Fixed reviewed read-only root commands; no dynamic remote selectors."""
    lines = ["set -Eeuo pipefail", "export LC_ALL=C"]
    stat_fmt = "%u|%g|%a|%h|%F|%d|%i|%s"
    exact_paths = (*ROOT_DIRECTORIES, *(f[0] for f in ROOT_FILES))
    # Check components explicitly: no implicit stat dereferencing ambiguity.
    for path in exact_paths:
        lines.append(f"/usr/bin/sudo -n /usr/bin/test ! -L {path}")
    for path in exact_paths:
        lines.append(f"/usr/bin/printf 'STAT|{path}|' ")
        lines.append(f"/usr/bin/sudo -n /usr/bin/stat -c '{stat_fmt}' -- {path}")
    for path, _, _ in ROOT_FILES:
        lines.append(f"/usr/bin/printf 'HASH|{path}|' ")
        lines.append(f"/usr/bin/sudo -n /usr/bin/sha256sum -- {path}")
    for path, _, _ in ROOT_FILES:
        lines.append(f"/usr/bin/printf 'STAT|{path}|' ")
        lines.append(f"/usr/bin/sudo -n /usr/bin/stat -c '{stat_fmt}' -- {path}")
    for path in exact_paths:
        lines.append(f"/usr/bin/sudo -n /usr/bin/test ! -L {path}")
    for path in ROOT_STAGES:
        # Unlike 'test -e' alone, both positive absence checks fail closed
        # on sudo denial and reject dangling stage symlinks.
        lines.append(f"/usr/bin/sudo -n /usr/bin/test ! -e {path}")
        lines.append(f"/usr/bin/sudo -n /usr/bin/test ! -L {path}")
    lines.append("/usr/bin/printf 'STAGES|ABSENT\\n'")
    return ("\n".join(lines) + "\n").encode("ascii")


def run_bounded_command(argv, payload, *, timeout_s, output_cap):
    """Hard-cap SSH stdout and total elapsed time, killing a stuck process."""
    _require(type(argv) is list and argv and all(type(v) is str for v in argv) and
             type(payload) is bytes and len(payload) <= 16384 and
             type(timeout_s) in (int, float) and 0 < timeout_s <= 45 and
             type(output_cap) is int and 0 < output_cap <= _MAX_WITNESS_BYTES,
             "SSH_CALL_SHAPE_UNVERIFIED")
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,
                                   start_new_session=True, close_fds=True)
    except OSError as exc:
        raise R302Hold("SSH_START_UNVERIFIED") from exc
    captured = bytearray()
    selector = selectors.DefaultSelector()
    try:
        deadline = time.monotonic() + timeout_s
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        sent = 0
        if payload:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()
        stdout_open = True
        while stdout_open or sent < len(payload):
            remaining = deadline - time.monotonic()
            _require(remaining > 0, "SSH_TIMEOUT")
            events = selector.select(min(remaining, 0.5))
            if not events:
                continue
            for key, _mask in events:
                if key.data == "stdin":
                    try:
                        count = os.write(process.stdin.fileno(), payload[sent:sent + 4096])
                    except BlockingIOError:
                        continue
                    except (OSError, BrokenPipeError) as exc:
                        raise R302Hold("SSH_STDIN_UNVERIFIED") from exc
                    sent += count
                    if sent == len(payload):
                        selector.unregister(process.stdin)
                        process.stdin.close()
                else:
                    piece = os.read(process.stdout.fileno(),
                                    min(4096, output_cap + 1 - len(captured)))
                    if not piece:
                        stdout_open = False
                        selector.unregister(process.stdout)
                    else:
                        captured.extend(piece)
                        _require(len(captured) <= output_cap, "SSH_OUTPUT_UNBOUNDED")
            _require(stdout_open or sent == len(payload), "SSH_STDIN_UNVERIFIED")
        remaining = deadline - time.monotonic()
        _require(remaining > 0, "SSH_TIMEOUT")
        try:
            result = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise R302Hold("SSH_TIMEOUT") from exc
        _require(result == 0, "SSH_REMOTE_STATUS_UNVERIFIED")
        return bytes(captured)
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()


def physical_preflight(env, *, ssh_call=None):
    validate_environment(env)
    if ssh_call is None:
        ssh_call = run_bounded_command
    with tempfile.TemporaryDirectory(prefix="rc6-r302-readonly-") as temp:
        key_path, hosts_path = Path(temp) / "key", Path(temp) / "known_hosts"
        key_path.write_text(env["DO_SSH_KEY"] + "\n" if not env["DO_SSH_KEY"].endswith("\n")
                            else env["DO_SSH_KEY"], encoding="utf-8")
        hosts_path.write_text(env["DO_KNOWN_HOSTS"] + "\n" if not env["DO_KNOWN_HOSTS"].endswith("\n")
                              else env["DO_KNOWN_HOSTS"], encoding="utf-8")
        key_path.chmod(0o600)
        hosts_path.chmod(0o600)
        ssh_argv = [
            "/usr/bin/ssh", "-T", "-F", "/dev/null", "-i", str(key_path),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={hosts_path}",
            "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
            "-o", "ControlMaster=no", "-o", "ClearAllForwardings=yes",
            "-o", "PermitLocalCommand=no",
            f"{env['DO_USER']}@{env['DO_HOST']}", "/usr/bin/bash -s",
        ]
        identity = ssh_call(ssh_argv, _identity_script(), timeout_s=22, output_cap=96)
        verify_host_identity(identity, env)
        observed = ssh_call(ssh_argv, _witness_script(), timeout_s=45,
                            output_cap=_MAX_WITNESS_BYTES)
        return verify_witness(observed)


def main(argv=None, *, env=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    try:
        _require(argv == ["--physical"], "CLI_SHAPE_UNVERIFIED")
        result = physical_preflight(env)
        print("RC6_R302_BOOTSTRAP_RECEIPT=" + result +
              "|host_control_verified=NO_VERIFICADO|root_bundle_ready=NO|"
              "runtime_verified=NO_VERIFICADO|deploy_authorized=NO")
        return 0
    except R302Hold as exc:
        print("RC6_R302_BOOTSTRAP_RECEIPT=HOLD79|reason=" + exc.reason +
              "|host_control_verified=NO_VERIFICADO|root_bundle_ready=NO|"
              "deploy_authorized=NO")
        return 79
    except (OSError, UnicodeError, ValueError, TypeError):
        print("RC6_R302_BOOTSTRAP_RECEIPT=HOLD79|reason=UNHANDLED_FAIL_CLOSED|"
              "host_control_verified=NO_VERIFICADO|root_bundle_ready=NO|"
              "deploy_authorized=NO")
        return 79


if __name__ == "__main__":
    sys.exit(main())
