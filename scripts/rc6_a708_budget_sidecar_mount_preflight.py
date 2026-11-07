"""A708: metadata-only proof of the *existing* shared PPI budget bind mount.

This helper is streamed from Actions. It never opens SQLite, creates a budget,
imports the PPI SDK, uses network access, or modifies the Droplet/Watch.
Presence is deliberately NOT budget readiness: A630 still checks committed
budget, account-wide custody and the EXIT reserve immediately before any GET.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys


SCHEMA = "rc6.a708.ppi-budget-mount-preflight.v1"
HOST_ROOT = Path("/opt/porota-trading/data")
CONTAINER_ROOT = Path("/app/data")
RELATIVE_SIDECAR = Path("paper_v17/artifacts/observer_v17.db/ppi-budget/global.sqlite")
EXPECTED_CONTAINER_DB = "/app/data/paper_v17/observer_v17.db"
EXPECTED_RUNTIME_UID = 1000

REASONS = frozenset({
    "PPI_BUDGET_SIDECAR_ABSENT",
    "PPI_BUDGET_SIDECAR_PATH_UNSAFE",
    "PPI_BUDGET_SIDECAR_OWNER_UNVERIFIED",
    "PPI_BUDGET_SIDECAR_MOUNT_MISMATCH",
    "PPI_BUDGET_SIDECAR_PREFLIGHT_UNAVAILABLE",
    "PPI_BUDGET_SIDECAR_PATH_BOUND_ONLY",
})


def _hold(reason: str) -> dict:
    return {"schema": SCHEMA, "state": "HOLD", "reason": reason,
            "dev": None, "inode": None, "uid": None,
            "provider_requests": 0, "sqlite_connections": 0,
            "budget_ready": False}


def inspect_path(root: Path, *, expected_uid: int = EXPECTED_RUNTIME_UID) -> dict:
    """lstat only; reject aliases at every component without resolving links."""
    root = Path(root)
    path = root / RELATIVE_SIDECAR
    try:
        if not (root.is_absolute() and path.is_relative_to(root)):
            return _hold("PPI_BUDGET_SIDECAR_PATH_UNSAFE")
        # Reject aliases above /app/data or /opt/porota-trading/data too.
        # A bind can point at an unexpected root despite safe-looking children.
        components = list(reversed(root.parents)) + [root]
        node = root
        for part in RELATIVE_SIDECAR.parts:
            node = node / part
            components.append(node)
        for directory in components[:-1]:
            mode = directory.lstat().st_mode
            if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                return _hold("PPI_BUDGET_SIDECAR_PATH_UNSAFE")
        before = components[-1].lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                bool(stat.S_IMODE(before.st_mode) & 0o002)):
            return _hold("PPI_BUDGET_SIDECAR_PATH_UNSAFE")
        if before.st_uid != expected_uid:
            return _hold("PPI_BUDGET_SIDECAR_OWNER_UNVERIFIED")
        # A concurrent replacement between metadata reads cannot certify a
        # shared inode. A630 must independently recheck before any lease/GET.
        after = components[-1].lstat()
        if (before.st_dev, before.st_ino, before.st_uid, before.st_nlink) != (
                after.st_dev, after.st_ino, after.st_uid, after.st_nlink):
            return _hold("PPI_BUDGET_SIDECAR_PATH_UNSAFE")
        return {"schema": SCHEMA, "state": "PRESENT_UNVERIFIED", "reason":
                "PPI_BUDGET_SIDECAR_PATH_BOUND_ONLY", "dev": before.st_dev,
                "inode": before.st_ino, "uid": before.st_uid,
                "provider_requests": 0, "sqlite_connections": 0,
                "budget_ready": False}
    except FileNotFoundError:
        return _hold("PPI_BUDGET_SIDECAR_ABSENT")
    except (OSError, ValueError, TypeError):
        return _hold("PPI_BUDGET_SIDECAR_PREFLIGHT_UNAVAILABLE")


def observe(role: str, *, environment=None) -> dict:
    env = os.environ if environment is None else environment
    if role == "container":
        # Never inject this variable with docker exec: inspect the actual
        # product runtime's DB selection rather than masking a wrong mount.
        if (env.get("PAPER_V17_DB_PATH") != EXPECTED_CONTAINER_DB or
                os.geteuid() != EXPECTED_RUNTIME_UID):
            return _hold("PPI_BUDGET_SIDECAR_MOUNT_MISMATCH")
        return inspect_path(CONTAINER_ROOT)
    if role == "host":
        return inspect_path(HOST_ROOT)
    return _hold("PPI_BUDGET_SIDECAR_PREFLIGHT_UNAVAILABLE")


def compare(host: object, container: object) -> dict:
    """Require same regular inode through both namespaces; NEVER imply READY."""
    required = {"schema", "state", "reason", "dev", "inode", "uid",
                "provider_requests", "sqlite_connections", "budget_ready"}
    def valid(receipt):
        if (type(receipt) is not dict or set(receipt) != required or
                receipt.get("schema") != SCHEMA or
                receipt.get("state") not in {"HOLD", "PRESENT_UNVERIFIED"} or
                receipt.get("reason") not in REASONS or
                receipt.get("provider_requests") != 0 or
                receipt.get("sqlite_connections") != 0 or
                receipt.get("budget_ready") is not False):
            return False
        if receipt["state"] == "HOLD":
            return (receipt["reason"] != "PPI_BUDGET_SIDECAR_PATH_BOUND_ONLY" and
                    all(receipt[key] is None for key in ("dev", "inode", "uid")))
        return (receipt["reason"] == "PPI_BUDGET_SIDECAR_PATH_BOUND_ONLY" and
                all(type(receipt[key]) is int and receipt[key] >= 1
                    for key in ("dev", "inode", "uid")))

    if not valid(host):
        return public("PPI_BUDGET_SIDECAR_PREFLIGHT_UNAVAILABLE")
    if host["state"] == "HOLD":
        # Do not open a container connection when the host proves absence.
        return public(host["reason"])
    if not valid(container):
        return public("PPI_BUDGET_SIDECAR_PREFLIGHT_UNAVAILABLE")
    if container["state"] == "HOLD":
        return public(container["reason"])
    if (host["dev"], host["inode"], host["uid"]) != (
            container["dev"], container["inode"], container["uid"]):
        return public("PPI_BUDGET_SIDECAR_MOUNT_MISMATCH")
    return public("PPI_BUDGET_SIDECAR_PATH_BOUND_ONLY", path_matched=True)


def public(reason: str, *, path_matched=False) -> dict:
    return {"schema": SCHEMA, "state": "PATH_MATCHED_NOT_READY" if path_matched else "HOLD",
            "reason": reason, "path_mount_identity_verified": path_matched,
            "budget_readiness": "NO_VERIFICADO", "exit_reserve": "NO_VERIFICADO",
            "provider_requests": 0, "sqlite_connections": 0,
            "ppi_watch_mutations": 0, "orders_sent_by_this_action": 0}


def _load_receipt(path: str) -> object:
    try:
        target = Path(path)
        if not target.is_file() or not 0 < target.stat().st_size <= 4096:
            return None
        def unique(items):
            out = {}
            for key, value in items:
                if key in out:
                    raise ValueError("DUPLICATE_KEY")
                out[key] = value
            return out
        return json.loads(target.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, ValueError, UnicodeError, TypeError):
        return None


def main(args=None) -> int:
    args = sys.argv[1:] if args is None else args
    os.umask(0o077)
    if args == ["observe-host"] or args == ["observe-container"]:
        result = observe("host" if args[0] == "observe-host" else "container")
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["state"] == "PRESENT_UNVERIFIED" else 2
    if len(args) == 3 and args[0] == "compare":
        result = compare(_load_receipt(args[1]), _load_receipt(args[2]))
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["state"] == "PATH_MATCHED_NOT_READY" else 2
    print(json.dumps(public("PPI_BUDGET_SIDECAR_PREFLIGHT_UNAVAILABLE"), sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
