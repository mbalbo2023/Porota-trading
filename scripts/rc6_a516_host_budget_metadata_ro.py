"""A516/A519/A620: classify PPI sidecar, auxiliary and PID1 launch policy.

Streamed through the existing manual A331 GitHub Action to the pinned PAPER
observer container. This diagnostic reads only the global budget sidecar through
the product's existing read-only snapshot function. It never opens the trading
database, acquires a budget lease, calls PPI, or changes PPI Watch.

An OBSERVED snapshot or APPROVED policy-file mode grants no Book GET or trade.
The auxiliary docker-exec policy cannot attest the running observer's policy.
A620 observes only PID1's initial environment and immutable default policy;
this is not an assertion of effective process memory or live EXIT authority.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import stat


SCHEMA = "rc6.a516.ppi.budget.metadata-only.v3"
PRODUCT_SHA = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"
SIDECAR_KINDS = frozenset({"ABSENT", "REGULAR_SINGLE_LINK", "UNSAFE_OR_ALIAS", "UNAVAILABLE"})
SNAPSHOT_STATES = frozenset({"ABSENT", "OBSERVED", "DEGRADED", "UNVERIFIED"})
CAPACITY_MODES = frozenset({"APPROVED", "SHADOW", "OFF", "OTHER_OR_UNSET"})
POLICY_FILE_MODES = frozenset({"APPROVED", "SHADOW", "OFF", "UNAVAILABLE", "UNINSPECTED"})
EXEC_ENV_MODES = frozenset({"APPROVED", "SHADOW", "OFF", "INVALID", "UNSET", "UNINSPECTED"})
REQUESTED_MODES = frozenset({"APPROVED", "SHADOW", "OFF", "INVALID", "UNAVAILABLE", "UNINSPECTED"})
POLICY_SELECTORS = frozenset({"CONTAINER_DEFAULT", "CONTAINER_OVERRIDE", "UNINSPECTED"})
SUSPENSION_STATES = frozenset({"YES", "NO", "UNKNOWN"})
PID1_COMMAND_STATES = frozenset({"EXPECTED_BV_RUNTIME", "UNEXPECTED", "UNAVAILABLE", "UNINSPECTED"})
PID1_ENV_BYTES = 16 * 1024
PID1_COMMAND_BYTES = 2048
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


def auxiliary_policy_intent(environ=None, *, policy_loader=None) -> dict:
    """Classify config visible to *this docker exec*, not the observer process.

    The product's strict policy parser reads only its bounded policy JSON; no
    runtime ledger, approval artifacts, budget writer, or provider is opened.
    Even a configured APPROVED mode grants neither dynamic capacity nor a GET.
    """
    env = os.environ if environ is None else environ
    source = ("CONTAINER_OVERRIDE" if "POROTA_CAPACITY_POLICY_PATH" in env
              else "CONTAINER_DEFAULT")
    raw = env.get("POROTA_DYNAMIC_CAPACITY_MODE")
    if raw is None:
        env_mode = "UNSET"
    elif isinstance(raw, str) and raw.upper() in {"APPROVED", "SHADOW", "OFF"}:
        env_mode = raw.upper()
    else:
        env_mode = "INVALID"
    try:
        if policy_loader is None:
            from rc6_dynamic_universe.promotion import POLICY_PATH, read_json, validate_policy
            selected = env.get("POROTA_CAPACITY_POLICY_PATH", str(POLICY_PATH))
            policy = validate_policy(read_json(selected, limit=16 * 1024))
        else:
            policy = policy_loader(env.get("POROTA_CAPACITY_POLICY_PATH"))
        file_mode = policy["mode"]
        if not isinstance(file_mode, str) or file_mode not in {"OFF", "SHADOW", "APPROVED"}:
            raise ValueError("A519_POLICY_NOT_VALIDATED")
    except Exception:
        # Never echo a path, parser error, policy contents, env value or token.
        file_mode = "UNAVAILABLE"
    requested = ("UNAVAILABLE" if file_mode == "UNAVAILABLE" else
                 "INVALID" if env_mode == "INVALID" else
                 file_mode if env_mode == "UNSET" else env_mode)
    return {"policy_file_mode": file_mode, "exec_environment_mode": env_mode,
            "auxiliary_requested_mode": requested, "policy_path_selector": source}


def _read_procfile_bounded(path: str, limit: int) -> bytes:
    """One bounded procfs read; no child processes, logs, secrets or mutations."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        value = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if not value or len(value) > limit or not value.endswith(b"\x00"):
        raise ValueError("A620_PROCFILE_UNAVAILABLE")
    return value


def _selected_pid1_environment(raw: bytes) -> dict:
    """Drop all unrelated entries in memory; never retain/return credential values."""
    if not raw or len(raw) > PID1_ENV_BYTES or not raw.endswith(b"\x00"):
        raise ValueError("A620_PID1_ENV_UNAVAILABLE")
    selected = {}
    for entry in raw[:-1].split(b"\x00"):
        key, sep, value = entry.partition(b"=")
        if not sep or not key:
            raise ValueError("A620_PID1_ENV_INVALID")
        if key not in {b"POROTA_DYNAMIC_CAPACITY_MODE", b"POROTA_CAPACITY_POLICY_PATH"}:
            continue
        if len(value) > 512 or key in selected:
            raise ValueError("A620_PID1_ENV_AMBIGUOUS")
        selected[key] = value.decode("ascii", errors="strict")
    return {key.decode("ascii"): value for key, value in selected.items()}


def _unknown_pid1(command: str) -> dict:
    return {"observer_pid1_command": command,
            "observer_pid1_environment_mode": "UNINSPECTED",
            "observer_pid1_policy_file_mode": "UNINSPECTED",
            "observer_pid1_launch_requested_mode": "UNINSPECTED",
            "observer_pid1_policy_selector": "UNINSPECTED"}


def observer_pid1_launch_intent(*, proc_reader=None, policy_loader=None) -> dict:
    """Attest immutable container PID1 *launch* intent, not the effective runtime.

    Product 7e1e744d starts `python bv_paper_runtime.py` as PID1. Its
    scanner and EXIT children inherit initial environment, but Python may
    subsequently modify os.environ; /proc/1/environ is not live Python state.
    Any failure or unexpected topology is an explicit NO_VERIFICADO.

    Never inspect private env values except two allowlisted capacity selectors.
    A policy PATH override is not opened, even if supplied by PID1, because an
    arbitrary path may target a secret. The product's default policy is read
    through its strict 16 KiB read_json/validate_policy contract only.
    """
    reader = _read_procfile_bounded if proc_reader is None else proc_reader
    try:
        command = reader("/proc/1/cmdline", PID1_COMMAND_BYTES)
    except (OSError, ValueError, TypeError):
        return _unknown_pid1("UNAVAILABLE")
    if command not in {
            b"python\x00bv_paper_runtime.py\x00",
            b"/usr/local/bin/python\x00bv_paper_runtime.py\x00",
            b"python3\x00bv_paper_runtime.py\x00"}:
        return _unknown_pid1("UNEXPECTED")
    try:
        env = _selected_pid1_environment(reader("/proc/1/environ", PID1_ENV_BYTES))
    except (OSError, ValueError, TypeError, UnicodeError):
        return _unknown_pid1("EXPECTED_BV_RUNTIME")
    if "POROTA_CAPACITY_POLICY_PATH" in env:
        # Do not dereference arbitrary container/secret paths in a diagnostic.
        mode = env.get("POROTA_DYNAMIC_CAPACITY_MODE")
        environment = ("UNSET" if mode is None else mode.upper()
                       if mode.upper() in {"OFF", "SHADOW", "APPROVED"} else "INVALID")
        return {"observer_pid1_command": "EXPECTED_BV_RUNTIME",
                "observer_pid1_environment_mode": environment,
                "observer_pid1_policy_file_mode": "UNAVAILABLE",
                "observer_pid1_launch_requested_mode": "UNAVAILABLE",
                "observer_pid1_policy_selector": "CONTAINER_OVERRIDE"}
    aux = auxiliary_policy_intent(env, policy_loader=policy_loader)
    return {"observer_pid1_command": "EXPECTED_BV_RUNTIME",
            "observer_pid1_environment_mode": aux["exec_environment_mode"],
            "observer_pid1_policy_file_mode": aux["policy_file_mode"],
            "observer_pid1_launch_requested_mode": aux["auxiliary_requested_mode"],
            "observer_pid1_policy_selector": aux["policy_path_selector"]}


def build_receipt(kind: str, snapshot: dict, mode: str, *, policy_intent=None,
                  pid1_launch_intent=None) -> dict:
    """Project trusted fixed enums, never JSON content, paths, or account info."""
    if kind not in SIDECAR_KINDS or mode not in CAPACITY_MODES:
        raise ValueError("A516_INPUT_NOT_ALLOWLISTED")
    if policy_intent is None:
        policy_intent = {"policy_file_mode": "UNINSPECTED",
            "exec_environment_mode": "UNINSPECTED", "auxiliary_requested_mode": "UNINSPECTED",
            "policy_path_selector": "UNINSPECTED"}
    if (not isinstance(policy_intent, dict)
            or set(policy_intent) != {"policy_file_mode", "exec_environment_mode",
                "auxiliary_requested_mode", "policy_path_selector"}
            or policy_intent["policy_file_mode"] not in POLICY_FILE_MODES
            or policy_intent["exec_environment_mode"] not in EXEC_ENV_MODES
            or policy_intent["auxiliary_requested_mode"] not in REQUESTED_MODES
            or policy_intent["policy_path_selector"] not in POLICY_SELECTORS):
        raise ValueError("A519_POLICY_INTENT_NOT_ALLOWLISTED")
    if pid1_launch_intent is None:
        pid1_launch_intent = _unknown_pid1("UNINSPECTED")
    if (not isinstance(pid1_launch_intent, dict)
            or set(pid1_launch_intent) != { "observer_pid1_command",
                "observer_pid1_environment_mode", "observer_pid1_policy_file_mode",
                "observer_pid1_launch_requested_mode", "observer_pid1_policy_selector" }
            or pid1_launch_intent["observer_pid1_command"] not in PID1_COMMAND_STATES
            or pid1_launch_intent["observer_pid1_environment_mode"] not in EXEC_ENV_MODES
            or pid1_launch_intent["observer_pid1_policy_file_mode"] not in POLICY_FILE_MODES
            or pid1_launch_intent["observer_pid1_launch_requested_mode"] not in REQUESTED_MODES
            or pid1_launch_intent["observer_pid1_policy_selector"] not in POLICY_SELECTORS):
        raise ValueError("A620_PID1_INTENT_NOT_ALLOWLISTED")
    inspected = pid1_launch_intent["observer_pid1_command"] == "EXPECTED_BV_RUNTIME"
    selected = pid1_launch_intent["observer_pid1_policy_selector"]
    unknown = all(pid1_launch_intent[key] == "UNINSPECTED" for key in (
        "observer_pid1_environment_mode", "observer_pid1_policy_file_mode",
        "observer_pid1_launch_requested_mode", "observer_pid1_policy_selector"))
    if ((not inspected and not unknown) or (inspected and not unknown and
            selected == "CONTAINER_OVERRIDE" and (
                pid1_launch_intent["observer_pid1_policy_file_mode"] != "UNAVAILABLE" or
                pid1_launch_intent["observer_pid1_launch_requested_mode"] != "UNAVAILABLE"))):
        raise ValueError("A620_PID1_CONTEXT_CONFLICT")
    if inspected and not unknown and selected == "CONTAINER_DEFAULT":
        file_mode = pid1_launch_intent["observer_pid1_policy_file_mode"]
        environment = pid1_launch_intent["observer_pid1_environment_mode"]
        requested = ("UNAVAILABLE" if file_mode == "UNAVAILABLE" else
                     "INVALID" if environment == "INVALID" else
                     file_mode if environment == "UNSET" else environment)
        if (environment == "UNINSPECTED" or file_mode == "UNINSPECTED"
                or pid1_launch_intent["observer_pid1_launch_requested_mode"] != requested):
            raise ValueError("A620_PID1_DEFAULT_MODE_CONFLICT")
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
        **policy_intent,
        **pid1_launch_intent,
        "observer_launch_scope": "PID1_INITIAL_ENV_NOT_EFFECTIVE_PROCESS_STATE",
        "policy_observation_scope": "AUXILIARY_DOCKER_EXEC_NOT_OBSERVER_PROCESS",
        "runtime_effective_policy": "NO_VERIFICADO",
        "runtime_ledger_and_exit_reserves": "NO_VERIFICADO",
        "runtime_receipt_retention": "NO_VERIFICADO",
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
            value["capacity_environment_mode"],
            policy_intent={field: value[field] for field in (
                "policy_file_mode", "exec_environment_mode",
                "auxiliary_requested_mode", "policy_path_selector")},
            pid1_launch_intent={field: value[field] for field in (
                "observer_pid1_command", "observer_pid1_environment_mode",
                "observer_pid1_policy_file_mode", "observer_pid1_launch_requested_mode",
                "observer_pid1_policy_selector")})
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
            runtime_budget_snapshot(database), capacity_mode(),
            policy_intent=auxiliary_policy_intent(),
            pid1_launch_intent=observer_pid1_launch_intent())
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
