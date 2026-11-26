"""Read-only inventory of the installed RC6 IOL release and its governed host units."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from rc6_scheduler_snapshot import execution_state, instant, monotonic_target, systemd_clock
from scripts.porota_rootless_host import Hold, decode, no_alias, read_regular, require


SCHEMA = "porota.iol.installed-inventory.v1"
ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "ops/policy/host-control-plane-reconciliation-v2.json"
UNIT = re.compile(r"porota-[A-Za-z0-9_.@-]+\.(?:timer|service)\Z")
TARGET = re.compile(r"^Unit=(porota-[A-Za-z0-9_.@-]+\.service)$", re.MULTILINE)
PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "Unit", "Triggers",
    "LastTriggerUSec", "NextElapseUSecRealtime", "NextElapseUSecMonotonic", "WakeSystem",
    "Result", "ExecMainCode", "ExecMainStatus",
    "ExecMainStartTimestamp", "ExecMainExitTimestamp",
)
KNOWN_STATES = {
    "loaded", "masked", "not-found", "error", "bad-setting", "active", "inactive", "failed",
    "activating", "deactivating", "reloading", "running", "waiting", "elapsed", "dead", "exited",
    "start", "start-pre", "start-post", "stop", "stop-sigterm", "stop-sigkill", "auto-restart",
    "enabled", "enabled-runtime", "disabled", "static", "indirect", "generated", "alias",
    "linked", "linked-runtime", "masked-runtime", "transient", "success", "resources", "timeout",
    "exit-code", "signal", "core-dump", "watchdog", "start-limit-hit", "oom-kill", "exec-condition",
    "protocol", "dependency", "skip", "maintenance",
}
OBSERVER = Path("/app/data/paper_v17/observer_v17.db")
HISTORY = Path("/app/data/market_history.db")
OBSERVER_TABLES = (
    "observer_state", "paper_workspace", "candidate_identity_v2", "financial_instrument_catalog",
    "market_snapshots", "paper_decisions", "trade_gate_evaluations", "paper_positions", "paper_cauciones",
    "paper_future_positions", "candle_series", "candle_versions", "candle_samples", "candle_dirty",
    "candle_worker_state", "paper_notification_worker", "paper_supervisor_state",
)
HISTORY_TABLES = ("history_versions_v2", "history_canonical_v2", "history_local_daily_v1", "local_eod_receipts_v1")
SOURCES = (
    "IOL_REST", "IOL_MCP", "IOL_REST_TRADE_SAMPLE", "PPI_PRODUCTION_HISTORY",
    "PPI_PRODUCTION_HISTORY_REPORTED", "PPI_GUARDED_CURRENT", "PPI_MARKETDATA_CURRENT",
    "DATA912", "BYMA_INTERVALORES", "A3_CEM_CLOSING",
)


def _controlled_units():
    policy = decode(read_regular(POLICY))
    require(policy.get("schema_version") == 2, "INVENTORY_UNIT_POLICY_UNVERIFIED")
    units = {Path(path).name: (row["role"], path) for path, row in policy["units"].items()}
    units.update({name: ("RESIDUAL", None) for name in policy["residual_host_units"]})
    units.update({name: ("EXTERNAL_RC6", None) for name in policy["external_host_units"]})
    units.update({name: ("RETIRED_LEGACY", None) for name in policy["legacy_retire_units"]})
    timers = {}
    for name, (role, path) in tuple(units.items()):
        if not name.endswith(".timer"):
            continue
        targets = TARGET.findall(read_regular(ROOT / path).decode()) if path else []
        require(len(targets) <= 1, "INVENTORY_TIMER_POLICY_UNVERIFIED")
        target = targets[0] if targets else name.removesuffix(".timer") + ".service"
        timers[name] = (role, target)
        units.setdefault(target, ("BOUND_SERVICE", None))
    require(timers and all(UNIT.fullmatch(name) and "ppi-watch" not in name.lower()
                           and "ppi_watch" not in name.lower() for name in units),
            "INVENTORY_UNIT_SCOPE_UNVERIFIED")
    return units, timers


def _show(units):
    """Systemctl may return 1 while supplying valid properties for absent units."""
    try:
        process = subprocess.run(
            ["/usr/bin/systemctl", "show", "--all", "--no-pager",
             "--property=" + ",".join(PROPERTIES), "--", *sorted(units)],
            capture_output=True, text=True, check=False, timeout=8,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {}
    if process.returncode not in {0, 1} or len(process.stdout) > 256 * 1024:
        return {}
    rows = {}
    for block in process.stdout.strip().split("\n\n"):
        properties = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        name = properties.get("Id")
        if name in units:
            rows[name] = properties
    return rows


def _state(value):
    return value if value in KNOWN_STATES else None


def _iso(value):
    parsed = systemd_clock(value) or instant(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed else None


def _service(properties, role, trigger=None):
    execution = execution_state(properties, trigger)
    completed = execution in {"COMPLETED", "SKIPPED_BY_CONDITION"}
    status = properties.get("ExecMainStatus", "")
    return {
        "role": role, "load": _state(properties.get("LoadState")),
        "active": _state(properties.get("ActiveState")), "sub": _state(properties.get("SubState")),
        "enabled": _state(properties.get("UnitFileState")), "execution": execution,
        "last_result": _state(properties.get("Result")) if completed else None,
        "exit_status": int(status) if completed and re.fullmatch(r"[0-9]{1,3}", status) else None,
        "started_at": _iso(properties.get("ExecMainStartTimestamp")),
        "finished_at": _iso(properties.get("ExecMainExitTimestamp")),
    }


def _scheduler_inventory():
    units, timers = _controlled_units()
    rows = _show(units)
    observed_timers = {}
    for name, (role, expected) in sorted(timers.items()):
        row = rows.get(name, {})
        target = row.get("Unit") or row.get("Triggers")
        trigger = row.get("LastTriggerUSec")
        clock_id = time.CLOCK_BOOTTIME if row.get("WakeSystem") == "yes" else time.CLOCK_MONOTONIC
        monotonic_at = monotonic_target(row.get("NextElapseUSecMonotonic"),
                                        wall=datetime.now(timezone.utc), monotonic=time.clock_gettime(clock_id))
        state = _service(row, role)
        state.update({
            "service_expected": expected, "service_observed": target if target in units else None,
            "binding": "MATCH" if target == expected else "MISMATCH" if target else "NO_VERIFICADO",
            "last_trigger_at": _iso(trigger), "next_realtime_at": _iso(row.get("NextElapseUSecRealtime")),
            "next_monotonic_at": monotonic_at.isoformat() if monotonic_at else None,
            "service": _service(rows.get(target, {}), units[target][0], trigger) if target in units else None,
        })
        for key in ("execution", "last_result", "exit_status", "started_at", "finished_at"):
            state.pop(key)
        observed_timers[name] = state
    services = {name: _service(rows.get(name, {}), role)
                for name, (role, _) in sorted(units.items()) if name.endswith(".service")}
    ppi_units = {}
    for name, row in {**services, **observed_timers}.items():
        if not name.startswith("porota-ppi-"):
            continue
        if row["active"] in {"active", "activating", "reloading"} or row["enabled"] in {"enabled", "enabled-runtime"}:
            state = "ACTIVE_OR_ENABLED"
        elif row["load"] == "not-found" or row["active"] in {"inactive", "failed"}:
            state = "INACTIVE_OR_ABSENT"
        else:
            state = "NO_VERIFICADO"
        ppi_units[name] = state
    return {"scope": "RC6_POLICY_EXACT_UNITS", "requested_units": len(units), "observed_units": len(rows),
            "timers": observed_timers, "services": services, "operational_ppi_units": ppi_units}


def _read_query(connection, deadline, query, parameters=()):
    until = min(deadline, time.monotonic() + 0.7)
    connection.set_progress_handler(lambda: int(time.monotonic() >= until), 1000)
    try:
        if time.monotonic() < until:
            return connection.execute(query, parameters).fetchall()
    except sqlite3.Error:
        pass
    return None


def _scalar(connection, deadline, query, parameters=()):
    rows = _read_query(connection, deadline, query, parameters)
    return rows[0][0] if rows else None


def _sources(connection, deadline, table, expression, group_by=()):
    placeholders = ",".join("?" for _ in SOURCES)
    groups = ",".join((*group_by, f"CASE WHEN {expression} IN ({placeholders}) THEN {expression} ELSE 'OTHER' END"))
    rows = _read_query(connection, deadline, f"SELECT {groups}, COUNT(*) FROM {table} GROUP BY {groups}", SOURCES * 2)
    if rows is None:
        return None
    return [{"source": row[-2], "rows": row[-1], **({"resolution": row[0]} if group_by else {})} for row in rows]


def _observe_database(path, expected, deadline, *, observer=False):
    try:
        filename = no_alias(path).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(filename, uri=True, timeout=0.2)) as connection:
            connection.execute("PRAGMA query_only=ON")
            query_only = connection.execute("PRAGMA query_only").fetchone()[0] == 1
            tables = _read_query(connection, deadline, "SELECT name FROM sqlite_master WHERE type='table'")
            if tables is None:
                return {"state": "NO_VERIFICADO"}
            present = {row[0] for row in tables} & set(expected)
            counts = {table: _scalar(connection, deadline, f"SELECT COUNT(*) FROM {table}") for table in sorted(present)}
            result = {"state": "OBSERVED", "query_only": query_only,
                      "schema_version": _scalar(connection, deadline, "PRAGMA schema_version"),
                      "tables_missing": sorted(set(expected) - present), "rows": counts}
            if observer:
                state = _read_query(connection, deadline, "SELECT mode,real_orders_sent,ppi_auth,heartbeat_at FROM observer_state WHERE id=1")
                if state and len(state) == 1:
                    mode, orders, ppi_auth, heartbeat = state[0]
                    result["paper_mode"] = mode if mode == "PRODUCTION_PAPER" else None
                    result["real_orders_sent"] = orders if type(orders) is int and orders >= 0 else None
                    result["ppi_auth"] = ppi_auth if ppi_auth in {"NOT_ATTEMPTED", "OK", "ERROR", "COOLDOWN"} else None
                    result["heartbeat_at"] = _iso(heartbeat)
                check = _scalar(connection, deadline, "PRAGMA quick_check('observer_state')")
                result["observer_state_quick_check"] = None if check is None else check == "ok"
                result["latest_candle_at"] = _iso(_scalar(connection, deadline,
                    "SELECT bar_end FROM candle_versions ORDER BY id DESC LIMIT 1"))
                source = "CASE WHEN json_valid(identity_json) THEN json_extract(identity_json, '$.source') END"
                resolution = ("CASE WHEN json_valid(identity_json) THEN CASE json_extract(identity_json, '$.resolution') "
                              "WHEN '1m' THEN '1m' WHEN '5m' THEN '5m' WHEN '1d' THEN '1d' ELSE 'OTHER' END ELSE 'OTHER' END")
                result["candle_series_by_source"] = _sources(connection, deadline, "candle_series", source, (resolution,))
            else:
                result["canonical_by_source"] = _sources(connection, deadline, "history_canonical_v2", "source")
                result["versions_by_source"] = _sources(connection, deadline, "history_versions_v2", "source")
            return result
    except (OSError, sqlite3.Error, Hold):
        return {"state": "NO_VERIFICADO"}


def _runtime_inventory():
    started = time.monotonic()
    return {
        "source_sha": os.getenv("POROTA_BUILD_SHA"), "source_tree": os.getenv("POROTA_CANDIDATE_TREE_SHA"),
        "python": ".".join(str(part) for part in sys.version_info[:3]), "sqlite": sqlite3.sqlite_version,
        "observer": _observe_database(OBSERVER, OBSERVER_TABLES, started + 3.5, observer=True),
        "history": _observe_database(HISTORY, HISTORY_TABLES, started + 7),
    }


def _dashboard(manager, current):
    cid = current["roles"]["dashboard"]
    try:
        raw = manager.docker.call("container", "inspect", cid, timeout=4)
        item, = decode(raw)
        if item.get("Id") != cid or item.get("Image") != current["image_id"]:
            return {"state": "NO_VERIFICADO"}
        state = item.get("State", {})
        bindings = item.get("NetworkSettings", {}).get("Ports", {}) or {}
        health = state.get("Health", {}).get("Status")
        return {"state": "OBSERVED", "running": state.get("Running") is True,
                "health": health if health in {"healthy", "unhealthy", "starting"} else None,
                "loopback_8000_published": bindings.get("8000/tcp") == [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
        return {"state": "NO_VERIFICADO"}


def run_host_inventory(manager, current):
    """Use the caller's verified rootless manager and release lock."""
    require(current.get("state") == "VALIDADO_RUNTIME" and current.get("market_provider") == "IOL_REST",
            "INSTALLED_IOL_RELEASE_REQUIRED")
    coordinates = {key: current[key] for key in ("source_sha", "source_tree", "image_id")}
    try:
        raw = manager.docker.call("container", "exec", current["roles"]["observer"], "timeout", "--kill-after=1s",
                                  "9s", "python", "-B", "-m", "scripts.rc6_iol_installed_inventory",
                                  "--runtime-db", timeout=11)
        runtime = decode(raw)
        require(isinstance(runtime, dict) and isinstance(runtime.get("observer"), dict) and
                all(runtime.pop(key, None) == coordinates[key] for key in ("source_sha", "source_tree")),
                "INVENTORY_RUNTIME_RELEASE_CHANGED")
    except (RuntimeError, OSError, ValueError, TypeError):
        runtime = {"state": "NO_VERIFICADO"}
    try:
        scheduler = _scheduler_inventory()
    except (Hold, OSError, ValueError, TypeError, KeyError):
        scheduler = {"state": "NO_VERIFICADO"}
    dashboard = _dashboard(manager, current)
    observer = runtime.get("observer", {})
    return {"schema": SCHEMA, **coordinates, "attempt": current["attempt"],
            "observed_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
            "ppi_watch_queried": False, "real_orders_sent": observer.get("real_orders_sent"),
            "scheduler": scheduler, "runtime": runtime, "dashboard": dashboard}


if __name__ == "__main__":
    if sys.argv[1:] != ["--runtime-db"] or os.getenv("POROTA_RC6_MARKET_PROVIDER") != "IOL_REST":
        raise SystemExit(2)
    print(json.dumps(_runtime_inventory(), sort_keys=True))
