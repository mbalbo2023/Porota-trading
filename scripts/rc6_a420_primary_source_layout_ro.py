#!/usr/bin/env python3
"""A420: bounded source-cycle layout receipt, not a source/DB reader.

Only fixed file roles, coarse ages, safe counts and fixed systemd state labels
can leave the droplet. No command-line path input, provider access, SQLite,
Docker, PPI Watch, secret files, process environment or order interfaces.

This distinguishes a missing optional primary JSON cache from a missing or
stale source-cycle *producer*. It does not establish why a DB fallback failed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
from zoneinfo import ZoneInfo


SCHEMA = "rc6.a420.primary-source-layout-readonly.v2"
STATE_SCHEMA = "rc6.source-cycle-dispatch.v1"
MARKET_ROOT = Path("/opt/porota-trading/data/market")
FILES = (
    ("PRIMARY_MARKET_CACHE", MARKET_ROOT / "primary_last.json", 12 * 1024 * 1024, False),
    ("PRIMARY_APP_CACHE", Path("/app/data/market/primary_last.json"), 12 * 1024 * 1024, False),
    ("SOURCE_CYCLE_STATE", MARKET_ROOT / "rc6_source_cycle_dispatch_state.json", 4096, True),
    ("FULL_UNIVERSE_EXPORT", MARKET_ROOT / "rc6_instrument_evidence_latest.json", 48 * 1024 * 1024, False),
    ("SOURCE_CYCLE_SCRIPT", Path("/opt/porota-trading/scripts/rc6_source_cycle_dispatch.py"), 2 * 1024 * 1024, False),
    ("FULL_UNIVERSE_SCRIPT", Path("/opt/porota-trading/scripts/rc6_full_universe_evidence.py"), 2 * 1024 * 1024, False),
    # The scanner is not deployed yet; MISSING means NO_VERIFICADO, never GREEN.
    ("PPI_SCANNER_PRIVATE_RECEIPT", MARKET_ROOT / "rc6_ppi_scanner_primary_latest.json", 2 * 1024 * 1024, False),
)
UNIT_NAMES = ("porota-iol-shadow-collector-rc6.timer",
              "porota-iol-shadow-collector-rc6.service")
ART = ZoneInfo("America/Argentina/Buenos_Aires")
SAFE_LOAD = {"loaded", "not-found", "masked", "error"}
SAFE_ACTIVE = {"active", "inactive", "failed", "activating", "deactivating", "reloading"}
SAFE_RESULT = {"success", "exit-code", "timeout", "signal", "core-dump",
               "watchdog", "start-limit-hit", "protocol", "resources", "dependency"}


class Rejected(Exception):
    """A fixed, sanitized reason, never the underlying path/message."""


def _open_bounded(path: Path, maximum: int) -> tuple[int, os.stat_result]:
    """Anchor *each* path component without following aliases or devices."""
    if not path.is_absolute() or ".." in path.parts or maximum <= 0:
        raise Rejected("UNSAFE_PATH")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = None
    fd = None
    try:
        directory = os.open("/", flags | os.O_DIRECTORY)
        for part in path.parts[1:-1]:
            next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        fd = os.open(path.name, flags | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Rejected("UNSAFE_FILE")
        if info.st_size > maximum:
            raise Rejected("SIZE_LIMIT")
        return fd, info
    except FileNotFoundError as exc:
        raise Rejected("MISSING") from exc
    except (NotADirectoryError, IsADirectoryError) as exc:
        raise Rejected("UNSAFE_PATH") from exc
    except OSError as exc:
        # This includes symlink ELOOP, blocked permission and inaccessible mount.
        raise Rejected("UNAVAILABLE_OR_ALIAS") from exc
    finally:
        if directory is not None:
            os.close(directory)
        if fd is not None and sys.exc_info()[0] is not None:
            os.close(fd)


def _age_bucket(value: float | None, now: datetime) -> str:
    if value is None:
        return "UNKNOWN"
    age = now.timestamp() - value
    if age < -30:
        return "FUTURE_CLOCK"
    if age < 300:
        return "WITHIN_5M"
    if age < 1800:
        return "WITHIN_30M"
    if age < 7200:
        return "WITHIN_2H"
    if age < 86400:
        return "WITHIN_DAY"
    return "OLDER_THAN_DAY"


def _state_receipt(raw: bytes, now: datetime) -> dict[str, str]:
    result = {"state_schema": "NO_VERIFICADO", "session": "NO_VERIFICADO",
              "public_last_success": "UNKNOWN", "evidence_last_success": "UNKNOWN"}
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
            return result
        result["state_schema"] = "MATCHED"
        local_day = now.astimezone(ART).date().isoformat()
        session = data.get("session_date")
        result["session"] = "TODAY" if session == local_day else "OTHER_DAY_OR_MISSING"
        for key, output in (("last_public_success_at", "public_last_success"),
                            ("last_evidence_success_at", "evidence_last_success")):
            value = data.get(key)
            if not isinstance(value, str):
                continue
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                result[output] = _age_bucket(parsed.timestamp(), now)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        pass
    return result


def inspect_file(role: str, path: Path, maximum: int, is_state: bool,
                 *, now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    receipt: dict[str, object] = {"role": role, "status": "NO_VERIFICADO",
                                   "mtime_age_bucket": "UNKNOWN"}
    fd = None
    try:
        fd, info = _open_bounded(path, maximum)
        receipt["status"] = "PRESENT_REGULAR"
        receipt["mtime_age_bucket"] = _age_bucket(info.st_mtime, now)
        if is_state:
            raw = os.read(fd, maximum + 1)
            final = os.fstat(fd)
            if (len(raw) != info.st_size or
                    (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) !=
                    (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)):
                raise Rejected("CHANGED_DURING_READ")
            receipt.update(_state_receipt(raw, now))
    except Rejected as exc:
        receipt.update(status=str(exc), mtime_age_bucket="UNKNOWN")
    finally:
        if fd is not None:
            os.close(fd)
    return receipt


def _verify_sidecar_access(path: Path, maximum: int, effective_user: str,
                           expected: os.stat_result) -> str:
    """DAC-only read probe: no bytes read, no shell, no persistent UID change.

    This cannot reproduce arbitrary systemd mount namespace restrictions.
    A real service-context proof remains NO_VERIFICADO until runtime receipt.
    """
    if os.geteuid() != 0 or effective_user not in {"ROOT", "UID1000"}:
        return "NO_VERIFICADO"
    if effective_user == "ROOT":
        # The existing host probe runs with sudo -n; _open_bounded already
        # succeeded as root and did not consume the private file's content.
        return "ACCESS_OK"
    if not hasattr(os, "fork"):
        return "NO_VERIFICADO"
    try:
        pid = os.fork()
    except OSError:
        return "NO_VERIFICADO"
    if pid == 0:
        # Child only. Never change the controlling host-probe process identity.
        try:
            try:
                gid = pwd.getpwuid(1000).pw_gid
            except KeyError:
                gid = 1000
            os.setgroups([])
            os.setgid(gid)
            os.setuid(1000)
            fd, actual = _open_bounded(path, maximum)
            os.close(fd)
            # Fail closed on a swap between the root metadata read and the
            # unprivileged DAC probe. A different regular file proves nothing.
            if ((actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns,
                 actual.st_uid, stat.S_IMODE(actual.st_mode)) !=
                (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns,
                 expected.st_uid, stat.S_IMODE(expected.st_mode))):
                os._exit(2)
            os._exit(0)
        except Rejected:
            os._exit(1)
        except (OSError, ValueError):
            os._exit(2)
    try:
        _, status = os.waitpid(pid, 0)
    except OSError:
        return "NO_VERIFICADO"
    if not os.WIFEXITED(status):
        return "NO_VERIFICADO"
    # A non-root open failure could be DAC, a disappearing file or a changed
    # mount; do not mislabel all of them as a definitive permission denial.
    return {0: "ACCESS_OK", 1: "READ_FAILED"}.get(os.WEXITSTATUS(status), "NO_VERIFICADO")


def inspect_private_receipt(path: Path, maximum: int, *, effective_user: str,
                            now: datetime | None = None,
                            expected_owner_uid: int = 1000) -> dict[str, object]:
    """Fixed-role, privacy-preserving metadata; never read quote bytes."""
    now = now or datetime.now(timezone.utc)
    result: dict[str, object] = {
        "role": "PPI_SCANNER_PRIVATE_RECEIPT", "status": "NO_VERIFICADO",
        "mtime_age_bucket": "UNKNOWN", "private_mode": "UNKNOWN",
        "owner_class": "UNKNOWN", "consumer_access": "NO_VERIFICADO",
        "access_scope": "HOST_DAC_ONLY",
    }
    fd = None
    try:
        fd, info = _open_bounded(path, maximum)
        result["mtime_age_bucket"] = _age_bucket(info.st_mtime, now)
        mode_ok = stat.S_IMODE(info.st_mode) == 0o600
        owner_ok = info.st_uid == expected_owner_uid
        result["private_mode"] = "PRIVATE_0600" if mode_ok else "OTHER"
        result["owner_class"] = "UID1000" if owner_ok else "OTHER"
        if not mode_ok:
            result["status"] = "UNSAFE_MODE"
        elif not owner_ok:
            result["status"] = "OWNER_MISMATCH"
        else:
            result["status"] = "PRESENT_REGULAR"
            result["consumer_access"] = _verify_sidecar_access(path, maximum, effective_user, info)
    except Rejected as exc:
        result["status"] = str(exc)
    finally:
        if fd is not None:
            os.close(fd)
    return result


def _safe_label(value: str | None, whitelist: set[str]) -> str:
    return value if value in whitelist else "UNKNOWN"


def _effective_user_class(user: str | None, dynamic_user: str | None) -> str:
    if dynamic_user == "yes":
        return "DYNAMIC"
    if dynamic_user != "no" or user is None:
        return "UNKNOWN"
    if user in ("", "root", "0"):
        return "ROOT"
    if user == "1000":
        return "UID1000"
    try:
        uid = int(user) if user.isascii() and user.isdecimal() else pwd.getpwnam(user).pw_uid
        return "ROOT" if uid == 0 else "UID1000" if uid == 1000 else "OTHER"
    except (KeyError, ValueError, OverflowError, OSError):
        return "UNKNOWN"


def inspect_unit(unit: str, *, runner=subprocess.run) -> dict[str, str]:
    """Use systemctl show ONLY. Never start, restart, enable, or inspect env."""
    result = {"unit": "CYCLE_TIMER" if unit.endswith(".timer") else "CYCLE_SERVICE",
              "query": "UNAVAILABLE", "load": "UNKNOWN", "active": "UNKNOWN",
              "result": "UNKNOWN", "main_exit": "UNKNOWN",
              "effective_user": "UNKNOWN", "dynamic_user": "UNKNOWN", "dropins": "UNKNOWN"}
    if unit not in UNIT_NAMES:
        return result
    try:
        completed = runner(["systemctl", "show", "--no-pager", "--property=LoadState",
                            "--property=ActiveState", "--property=Result",
                            "--property=ExecMainStatus", "--property=User",
                            "--property=DynamicUser", "--property=DropInPaths", unit],
                           capture_output=True, text=True, timeout=4, check=False)
        if completed.returncode != 0 or len(completed.stdout) > 4096:
            return result
        pairs = dict(line.split("=", 1) for line in completed.stdout.splitlines()
                     if "=" in line and len(line) <= 2048)
        result.update(query="READ_ONLY", load=_safe_label(pairs.get("LoadState"), SAFE_LOAD),
                      active=_safe_label(pairs.get("ActiveState"), SAFE_ACTIVE),
                      result=_safe_label(pairs.get("Result"), SAFE_RESULT))
        status = pairs.get("ExecMainStatus", "")
        if status.isascii() and status.isdecimal() and len(status) <= 4:
            result["main_exit"] = "ZERO" if int(status) == 0 else "NONZERO"
        if unit.endswith(".timer"):
            result.update(effective_user="NOT_APPLICABLE", dynamic_user="NOT_APPLICABLE",
                          dropins="NOT_APPLICABLE")
        else:
            dynamic = pairs.get("DynamicUser")
            result["dynamic_user"] = {"yes": "YES", "no": "NO"}.get(dynamic, "UNKNOWN")
            result["effective_user"] = (_effective_user_class(pairs.get("User"), dynamic)
                                        if result["load"] == "loaded" else "UNKNOWN")
            dropins = pairs.get("DropInPaths")
            result["dropins"] = ("PRESENT" if dropins else "NONE") if dropins is not None else "UNKNOWN"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return result


def inspect_all(*, now: datetime | None = None, files=FILES,
                runner=subprocess.run) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware time required")
    units = [inspect_unit(unit, runner=runner) for unit in UNIT_NAMES]
    consumer = units[-1]["effective_user"] if len(units) == len(UNIT_NAMES) else "UNKNOWN"
    inspected = [inspect_private_receipt(path, maximum, effective_user=consumer, now=now)
                 if role == "PPI_SCANNER_PRIVATE_RECEIPT"
                 else inspect_file(role, path, maximum, is_state, now=now)
                 for role, path, maximum, is_state in files]
    return {"schema": SCHEMA, "read_only": True, "host_metadata_only": True,
            "database_opened": False, "provider_calls": 0,
            "order_routes_called": False, "ppi_watch_touched": False,
            "paper_ready": False, "diagnostic_only": True,
            "files": inspected, "units": units}


def main(argv=None) -> int:
    if (sys.argv[1:] if argv is None else argv) != ["host-probe"]:
        print("Usage: primary source layout read-only host-probe", file=sys.stderr)
        return 64
    print(json.dumps(inspect_all(), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
