"""Read-only inventory of the installed RC6 IOL release and its governed host units."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import hashlib
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
PROBE_SOURCE_LIMIT = 64 * 1024
RUNTIME_OUTPUT_LIMIT = 32 * 1024
UNIT_OUTPUT_LIMIT = 8 * 1024
UNIT_PROBE_WORKERS = 6
SYSTEMCTL_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC", "SYSTEMD_COLORS": "0"}
CANONICAL_SOURCE_QUERY_SECONDS = 2.5
CANONICAL_SOURCE_GROUP_LIMIT = 128
RUNTIME_BOOTSTRAP = """import hashlib, json, sys
source, expected = sys.argv[1:]
actual = hashlib.sha256(source.encode('utf-8')).hexdigest()
if actual != expected:
    print(json.dumps({'state': 'NO_VERIFICADO', 'failure_signature': 'INVENTORY_PROBE_HASH_MISMATCH'}))
    raise SystemExit(0)
filename = '/app/scripts/rc6_iol_installed_inventory.py'
namespace = {'__file__': filename, '__name__': 'rc6_installed_inventory_probe'}
try:
    exec(compile(source, filename, 'exec'), namespace)
    result = namespace['_runtime_child_receipt']()
except Exception:
    result = {'state': 'NO_VERIFICADO', 'failure_signature': 'INVENTORY_RUNTIME_PROBE_CRASHED'}
result['probe_source_sha256'] = actual
print(json.dumps(result, sort_keys=True))
"""


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
    require(timers and len(units) <= 100 and all(UNIT.fullmatch(name) and "ppi-watch" not in name.lower()
                           and "ppi_watch" not in name.lower() for name in units),
            "INVENTORY_UNIT_SCOPE_UNVERIFIED")
    return units, timers


def _show_unit(unit):
    """Pin each response to one requested unit without relying on separators."""
    if unit.endswith("@.service"):
        return _show_template(unit)
    try:
        process = subprocess.run(
            ["/usr/bin/systemctl", "show", "--all", "--no-pager",
             "--property=" + ",".join(PROPERTIES), "--", unit],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False, timeout=2.5,
            env=SYSTEMCTL_ENV,
        )
    except subprocess.TimeoutExpired:
        return {}, "INVENTORY_UNIT_SHOW_TIMEOUT"
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {}, "INVENTORY_UNIT_SHOW_UNAVAILABLE"
    if not isinstance(process.stdout, str) or len(process.stdout) > UNIT_OUTPUT_LIMIT:
        return {}, "INVENTORY_UNIT_OUTPUT_INVALID"
    properties = {}
    for line in process.stdout.splitlines():
        name, separator, value = line.partition("=")
        if not separator or name not in PROPERTIES:
            continue
        if name in properties:
            return {}, "INVENTORY_UNIT_PROPERTY_DUPLICATE"
        properties[name] = value
    if process.returncode != 0 and not (process.returncode in {1, 4} and properties.get("LoadState") == "not-found"):
        return {}, "INVENTORY_UNIT_SHOW_EXIT_NONZERO"
    if properties.get("Id") != unit:
        return {}, "INVENTORY_UNIT_ID_MISMATCH"
    if properties.get("LoadState") not in KNOWN_STATES:
        return {}, "INVENTORY_UNIT_LOAD_STATE_UNVERIFIED"
    return properties, None


def _show_template(unit):
    """A template has unit-file metadata. Its instances own execution state."""
    try:
        process = subprocess.run(
            ["/usr/bin/systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager",
             "--plain", "--", unit], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=False, timeout=2.5, env=SYSTEMCTL_ENV,
        )
    except subprocess.TimeoutExpired:
        return {}, "INVENTORY_TEMPLATE_METADATA_TIMEOUT"
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {}, "INVENTORY_TEMPLATE_METADATA_UNAVAILABLE"
    if process.returncode != 0 or not isinstance(process.stdout, str) or len(process.stdout) > UNIT_OUTPUT_LIMIT:
        return {}, "INVENTORY_TEMPLATE_METADATA_UNVERIFIED"
    rows = [line.split() for line in process.stdout.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) not in {2, 3} or rows[0][0] != unit or rows[0][1] not in KNOWN_STATES:
        return {}, "INVENTORY_TEMPLATE_METADATA_UNVERIFIED"
    return {"Id": unit, "UnitFileState": rows[0][1], "TemplateMetadataObserved": True}, None


def _show(units):
    rows, failures = {}, {}
    names = sorted(units)
    with ThreadPoolExecutor(max_workers=UNIT_PROBE_WORKERS) as executor:
        for unit, (properties, reason) in zip(names, executor.map(_show_unit, names)):
            if reason is None:
                rows[unit] = properties
            else:
                failures[unit] = reason
    return rows, failures


def _state(value):
    return value if value in KNOWN_STATES else None


def _iso(value):
    parsed = systemd_clock(value) or instant(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed else None


def _service(properties, role, trigger=None):
    execution = execution_state(properties, trigger)
    completed = execution in {"COMPLETED", "SKIPPED_BY_CONDITION"}
    status = properties.get("ExecMainStatus", "")
    result = {
        "role": role, "load": _state(properties.get("LoadState")),
        "active": _state(properties.get("ActiveState")), "sub": _state(properties.get("SubState")),
        "enabled": _state(properties.get("UnitFileState")), "execution": execution,
        "last_result": _state(properties.get("Result")) if completed else None,
        "exit_status": int(status) if completed and re.fullmatch(r"[0-9]{1,3}", status) else None,
        "started_at": _iso(properties.get("ExecMainStartTimestamp")),
        "finished_at": _iso(properties.get("ExecMainExitTimestamp")),
    }
    if properties.get("TemplateMetadataObserved") is True:
        result.update(evidence_kind="TEMPLATE_UNIT_FILE", execution="NOT_APPLICABLE_TEMPLATE")
    return result


def _scheduler_inventory():
    units, timers = _controlled_units()
    rows, failures = _show(units)
    observed_timers = {}
    for name, (role, expected) in sorted(timers.items()):
        row = rows.get(name, {})
        targets = row.get("Triggers", "").split()
        target = row.get("Unit") or (targets[0] if len(targets) == 1 else None)
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
            "state": "OBSERVED" if not failures else "PARTIAL" if rows else "NO_VERIFICADO",
            "unit_probe_failures": failures,
            "timers": observed_timers, "services": services, "operational_ppi_units": ppi_units}


def _bounded_query(connection, deadline, query, parameters=(), *, seconds=0.7):
    until = min(deadline, time.monotonic() + seconds)
    if time.monotonic() >= until:
        return None, "INVENTORY_DB_QUERY_BUDGET"
    connection.set_progress_handler(lambda: int(time.monotonic() >= until), 1000)
    try:
        return connection.execute(query, parameters).fetchall(), None
    except sqlite3.Error as exc:
        if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT or time.monotonic() >= until:
            return None, "INVENTORY_DB_QUERY_BUDGET"
        return None, "INVENTORY_DB_QUERY_UNAVAILABLE"
    finally:
        connection.set_progress_handler(None, 0)


def _read_query(connection, deadline, query, parameters=()):
    return _bounded_query(connection, deadline, query, parameters)[0]


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


def _canonical_sources(connection, deadline):
    # The canonical projection stores its own source next to the version ID.
    # Group the stored source once and only emit allowed labels (or OTHER).
    rows, reason = _bounded_query(connection, deadline, """SELECT source, COUNT(*)
        FROM history_canonical_v2 GROUP BY source LIMIT ?""",
        (CANONICAL_SOURCE_GROUP_LIMIT + 1,), seconds=CANONICAL_SOURCE_QUERY_SECONDS)
    if rows is None:
        return None, reason
    if len(rows) > CANONICAL_SOURCE_GROUP_LIMIT:
        return None, "INVENTORY_HISTORY_SOURCE_GROUP_LIMIT"
    counts = {}
    for source, count in rows:
        label = source if source in SOURCES else "OTHER"
        counts[label] = counts.get(label, 0) + count
    return [{"source": source, "rows": count} for source, count in sorted(counts.items())], None


def _observe_database(path, expected, deadline, *, observer=False):
    try:
        filename = no_alias(path).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(filename, uri=True, timeout=0.2)) as connection:
            connection.execute("PRAGMA query_only=ON")
            query_only = connection.execute("PRAGMA query_only").fetchone()[0] == 1
            tables = _read_query(connection, deadline, "SELECT name FROM sqlite_master WHERE type='table'")
            if tables is None:
                return {"state": "NO_VERIFICADO", "failure_signature": "INVENTORY_DB_SCHEMA_UNVERIFIED"}
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
                # CandleArchive stores UTC bar endings, including when old bars are revised later.
                result["latest_candle_at"] = _iso(_scalar(connection, deadline,
                    "SELECT MAX(bar_end) FROM candle_versions"))
                source = "CASE WHEN json_valid(identity_json) THEN json_extract(identity_json, '$.source') END"
                resolution = ("CASE WHEN json_valid(identity_json) THEN CASE json_extract(identity_json, '$.resolution') "
                              "WHEN '1m' THEN '1m' WHEN '5m' THEN '5m' WHEN '1d' THEN '1d' ELSE 'OTHER' END ELSE 'OTHER' END")
                result["candle_series_by_source"] = _sources(connection, deadline, "candle_series", source, (resolution,))
            else:
                canonical, canonical_failure = _canonical_sources(connection, deadline)
                result["canonical_by_source"] = canonical
                if canonical_failure is not None:
                    result["canonical_by_source_failure"] = canonical_failure
                result["versions_by_source"] = _sources(connection, deadline, "history_versions_v2", "source")
            return result
    except (OSError, sqlite3.Error, Hold):
        return {"state": "NO_VERIFICADO", "failure_signature": "INVENTORY_DB_READ_UNAVAILABLE"}


def _runtime_inventory():
    started = time.monotonic()
    return {
        "source_sha": os.getenv("POROTA_BUILD_SHA"), "source_tree": os.getenv("POROTA_CANDIDATE_TREE_SHA"),
        "python": ".".join(str(part) for part in sys.version_info[:3]), "sqlite": sqlite3.sqlite_version,
        "observer": _observe_database(OBSERVER, OBSERVER_TABLES, started + 3.5, observer=True),
        "history": _observe_database(HISTORY, HISTORY_TABLES, started + 9),
    }


def _runtime_child_receipt():
    if os.getenv("POROTA_RC6_MARKET_PROVIDER") != "IOL_REST":
        return {"state": "NO_VERIFICADO", "failure_signature": "INVENTORY_RUNTIME_PROVIDER_MISMATCH"}
    try:
        return _runtime_inventory()
    except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error):
        return {"state": "NO_VERIFICADO", "failure_signature": "INVENTORY_RUNTIME_PROBE_CRASHED"}


def _unavailable(reason, **fields):
    return {"state": "NO_VERIFICADO", "failure_signature": reason, **fields}


def _runtime_from_container(manager, current, coordinates, runtime_probe_source):
    if runtime_probe_source is None:
        args = ("-m", "scripts.rc6_iol_installed_inventory", "--runtime-db")
        expected_hash = None
    else:
        require(isinstance(runtime_probe_source, str) and bool(runtime_probe_source)
                and "\x00" not in runtime_probe_source and len(runtime_probe_source.encode("utf-8")) <= PROBE_SOURCE_LIMIT,
                "INVENTORY_PROBE_SOURCE_INVALID")
        expected_hash = hashlib.sha256(runtime_probe_source.encode("utf-8")).hexdigest()
        args = ("-c", RUNTIME_BOOTSTRAP, runtime_probe_source, expected_hash)
    try:
        result = manager.docker.command_result("container", "exec", current["roles"]["observer"],
                                               "timeout", "--kill-after=1s", "14s", "python", "-B", *args, timeout=17)
    except (RuntimeError, OSError, ValueError, TypeError) as exc:
        if getattr(exc, "public_details", {}).get("docker_failure", {}).get("result") == "TIMEOUT":
            return _unavailable("INVENTORY_RUNTIME_EXEC_TIMEOUT")
        return _unavailable("INVENTORY_RUNTIME_EXEC_UNAVAILABLE")
    if type(result.returncode) is not int or result.returncode != 0:
        code = result.returncode if type(result.returncode) is int and -255 <= result.returncode <= 255 else None
        reason = "INVENTORY_RUNTIME_EXEC_TIMEOUT" if code in {124, 137} else "INVENTORY_RUNTIME_EXEC_NONZERO"
        return _unavailable(reason, exec_exit_code=code)
    if not isinstance(result.stdout, str) or len(result.stdout) > RUNTIME_OUTPUT_LIMIT:
        return _unavailable("INVENTORY_RUNTIME_OUTPUT_INVALID")
    try:
        runtime = decode(result.stdout)
    except Hold:
        return _unavailable("INVENTORY_RUNTIME_JSON_INVALID")
    if not isinstance(runtime, dict):
        return _unavailable("INVENTORY_RUNTIME_OUTPUT_INVALID")
    if expected_hash is not None and runtime.get("probe_source_sha256") != expected_hash:
        return _unavailable("INVENTORY_PROBE_HASH_MISMATCH")
    if runtime.get("state") == "NO_VERIFICADO":
        reason = runtime.get("failure_signature")
        if reason not in {"INVENTORY_RUNTIME_PROVIDER_MISMATCH", "INVENTORY_RUNTIME_PROBE_CRASHED"}:
            reason = "INVENTORY_RUNTIME_OUTPUT_INVALID"
        return _unavailable(reason, **({"probe_source_sha256": expected_hash} if expected_hash else {}))
    if not isinstance(runtime.get("observer"), dict) or not isinstance(runtime.get("history"), dict):
        return _unavailable("INVENTORY_RUNTIME_OUTPUT_INVALID")
    if any(runtime.get(key) != coordinates[key] for key in ("source_sha", "source_tree")):
        return _unavailable("INVENTORY_RUNTIME_RELEASE_CHANGED")
    for key in ("source_sha", "source_tree"):
        runtime.pop(key)
    if expected_hash is not None:
        runtime["probe_source_sha256"] = expected_hash
    return runtime


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


def run_host_inventory(manager, current, *, runtime_probe_source=None):
    """Use the caller's verified rootless manager and release lock."""
    require(current.get("state") == "VALIDADO_RUNTIME" and current.get("market_provider") == "IOL_REST",
            "INSTALLED_IOL_RELEASE_REQUIRED")
    coordinates = {key: current[key] for key in ("source_sha", "source_tree", "image_id")}
    runtime = _runtime_from_container(manager, current, coordinates, runtime_probe_source)
    try:
        scheduler = _scheduler_inventory()
    except (Hold, OSError, ValueError, TypeError, KeyError):
        scheduler = _unavailable("INVENTORY_SCHEDULER_SCOPE_UNVERIFIED")
    dashboard = _dashboard(manager, current)
    observer = runtime.get("observer", {})
    return {"schema": SCHEMA, **coordinates, "attempt": current["attempt"],
            "observed_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
            "ppi_watch_queried": False, "real_orders_sent": observer.get("real_orders_sent"),
            "scheduler": scheduler, "runtime": runtime, "dashboard": dashboard}


if __name__ == "__main__":
    if sys.argv[1:] != ["--runtime-db"]:
        raise SystemExit(2)
    print(json.dumps(_runtime_child_receipt(), sort_keys=True))
