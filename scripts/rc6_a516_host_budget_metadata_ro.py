"""A516: classify the *existing* PPI budget sidecar without a provider call.

Streamed through the existing manual A331 GitHub Action to the pinned PAPER
observer container. This diagnostic reads only the global budget sidecar through
the product's existing read-only snapshot function. It never opens the trading
database, acquires a budget lease, calls PPI, or changes PPI Watch.

An OBSERVED snapshot is not authority to execute a Book GET or a PAPER trade.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import stat


SCHEMA = "rc6.a516.ppi.budget.metadata-only.v1"
PRODUCT_SHA = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"
SIDECAR_KINDS = frozenset({"ABSENT", "REGULAR_SINGLE_LINK", "UNSAFE_OR_ALIAS", "UNAVAILABLE"})
SNAPSHOT_STATES = frozenset({"ABSENT", "OBSERVED", "DEGRADED", "UNVERIFIED"})
CAPACITY_MODES = frozenset({"APPROVED", "SHADOW", "OFF", "OTHER_OR_UNSET"})
SUSPENSION_STATES = frozenset({"YES", "NO", "UNKNOWN"})
DIAGNOSES = frozenset({
    "SIDECAR_ABSENT", "SIDECAR_PATH_UNSAFE", "SNAPSHOT_UNAVAILABLE",
    "BUDGET_DEGRADED", "EXIT_SERVICE_SUSPENDED", "BUDGET_OBSERVED_NOT_AUTHORITY",
    "METADATA_INCONSISTENT", "HOST_PROBE_FAILED",
})


def sidecar_kind(path: Path) -> str:
    """Do not follow links or open either SQLite database."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return "UNSAFE_OR_ALIAS"
        return "REGULAR_SINGLE_LINK"
    except FileNotFoundError:
        return "ABSENT"
    except OSError:
        return "UNAVAILABLE"


def capacity_mode(environ=None) -> str:
    mode = (os.environ if environ is None else environ).get(
        "POROTA_DYNAMIC_CAPACITY_MODE", ""
    ).strip().upper()
    return mode if mode in {"APPROVED", "SHADOW", "OFF"} else "OTHER_OR_UNSET"


def build_receipt(kind: str, snapshot: dict, mode: str) -> dict:
    """Project trusted fixed enums, never JSON content, paths, or account info."""
    if kind not in SIDECAR_KINDS or mode not in CAPACITY_MODES:
        raise ValueError("A516_INPUT_NOT_ALLOWLISTED")
    if not isinstance(snapshot, dict) or snapshot.get("status") not in SNAPSHOT_STATES:
        raise ValueError("A516_SNAPSHOT_NOT_ALLOWLISTED")
    status = snapshot["status"]
    exit_state = snapshot.get("exit_service")
    lower = (exit_state.get("lower_suspended") if isinstance(exit_state, dict)
             else None)
    suspended = "YES" if lower is True else "NO" if lower is False else "UNKNOWN"
    if kind == "ABSENT":
        diagnosis = "SIDECAR_ABSENT" if status == "ABSENT" else "METADATA_INCONSISTENT"
    elif kind == "UNSAFE_OR_ALIAS":
        diagnosis = "SIDECAR_PATH_UNSAFE"
    elif kind == "UNAVAILABLE":
        diagnosis = "SNAPSHOT_UNAVAILABLE"
    elif status == "UNVERIFIED" or status == "ABSENT":
        diagnosis = "SNAPSHOT_UNAVAILABLE" if status == "UNVERIFIED" else "METADATA_INCONSISTENT"
    elif status == "DEGRADED":
        diagnosis = "BUDGET_DEGRADED"
    elif suspended == "YES":
        diagnosis = "EXIT_SERVICE_SUSPENDED"
    else:
        diagnosis = "BUDGET_OBSERVED_NOT_AUTHORITY"
    return {
        "schema": SCHEMA,
        "state": "EVIDENCE_ONLY_NO_GO",
        "expected_product_sha": PRODUCT_SHA,
        "sidecar_kind": kind,
        "snapshot_status": status,
        "capacity_environment_mode": mode,
        "exit_lower_suspended": suspended,
        "diagnosis": diagnosis,
        "snapshot_scope": "EXISTING_GLOBAL_PPI_SIDECAR_READ_ONLY",
        "trading_database_opened": False,
        "budget_sidecar_written": False,
        "budget_lease_acquired": False,
        "provider_calls": 0,
        "real_orders_sent": 0,
        "ppi_watch_touched": False,
        "book_replay_verified": False,
        "paper_ready": False,
    }


def verify_receipt(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        expected = build_receipt(value["sidecar_kind"],
            {"status": value["snapshot_status"],
             "exit_service": {"lower_suspended": {"YES": True, "NO": False,
                                                   "UNKNOWN": None}[value["exit_lower_suspended"]]}},
            value["capacity_environment_mode"])
        return (value == expected and set(value) == set(expected)
                and all(type(value[key]) is type(expected[key]) for key in expected)
                and value["diagnosis"] in DIAGNOSES)
    except (KeyError, TypeError, ValueError):
        return False


def main() -> int:
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    try:
        from _version import MODE, REAL_ORDER_CAPABILITY
        if (MODE != "PRODUCTION_PAPER" or REAL_ORDER_CAPABILITY != "BLOCKED"
                or os.getenv("RC6_A516_EXPECTED_PRODUCT_SHA") != PRODUCT_SHA):
            raise ValueError("A516_PAPER_IMAGE_GUARD_RED")
        from cg_paper_workspace import artifact_root, database_path
        from rc6_ppi_global_budget import runtime_budget_snapshot
        database = database_path()  # resolves path only: never sqlite.connect(database)
        sidecar = artifact_root(database) / "ppi-budget/global.sqlite"
        receipt = build_receipt(sidecar_kind(sidecar),
            runtime_budget_snapshot(database), capacity_mode())
        if not verify_receipt(receipt):
            raise ValueError("A516_RECEIPT_GUARD_RED")
    except Exception:
        receipt = {
            "schema": SCHEMA, "state": "NO_VERIFICADO",
            "diagnosis": "HOST_PROBE_FAILED", "expected_product_sha": PRODUCT_SHA,
            "trading_database_opened": False, "budget_sidecar_written": False,
            "budget_lease_acquired": False, "provider_calls": 0,
            "real_orders_sent": 0, "ppi_watch_touched": False,
            "book_replay_verified": False, "paper_ready": False,
        }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if receipt["state"] == "EVIDENCE_ONLY_NO_GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
