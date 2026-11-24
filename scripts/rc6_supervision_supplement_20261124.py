#!/usr/bin/env python3
"""Collect the published RC6 funnel and scheduler without opening SQLite."""

import argparse
import json
import os
from pathlib import Path
import re


SCHEMA = "rc6.supervision-current-readonly.v1"
CONTAINER_PROBE = r'''
import json
import os
from pathlib import Path
import re

from cg_paper_workspace import database_path
from rc6_scheduler_snapshot import systemd_clock
from rc6_shadow_runtime.funnel import STAGES
from rc6_trader_dashboard.datasets import artifact
from rc6_trader_dashboard.projection import Projection, Store


def snapshot_time(value):
    from datetime import datetime
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.isoformat() if stamp.tzinfo else None
    except ValueError:
        return None


def counts_by_stage(projection):
    cut = projection.shadow
    result = {"state": "NO_VERIFICADO", "by_currency_channel": [],
              "by_family_and_stage": "NOT_IN_COMPACT_CURRENT"}
    if not projection.shadow_fresh:
        result["reason"] = "CURRENT_NOT_FRESH_OR_UNAVAILABLE"
        return result
    report = cut["report"]
    funnel = report.get("operational_funnel", {})
    groups = funnel.get("by_currency_channel", [])
    if not isinstance(groups, list) or len(groups) > 64:
        result["reason"] = "CURRENT_FUNNEL_GROUPS_UNAVAILABLE"
        return result
    currencies = {"ARS", "USD", "USD_CCL", "USD_MEP", "USD_EXT", "EXT", "MEP", "CCL",
                  "EUR", "BRL", "CLP", "UYU", "JPY", "GBP", "CHF", "USDT", "USDC"}
    channels = {"NATIVE_FACTUAL", "UNIVERSE_SHADOW", "FAMILY_OBSERVE_ONLY",
                "SHADOW_ENTRY_EXPERIMENT"}
    for row in groups:
        if not isinstance(row, dict) or row.get("currency") not in currencies or row.get("channel") not in channels:
            result["reason"] = "CURRENT_FUNNEL_UNRECOGNIZED_COHORT"
            result["by_currency_channel"] = []
            return result
        stages = row.get("stages")
        if not isinstance(stages, dict) or any(key not in STAGES or type(value) is not int or not 0 <= value <= 120000
                                                for key, value in stages.items()):
            result["reason"] = "CURRENT_FUNNEL_COUNTS_INVALID"
            result["by_currency_channel"] = []
            return result
        result["by_currency_channel"].append({"currency": row["currency"], "channel": row["channel"],
                                               "stages": {stage: stages.get(stage, 0) for stage in STAGES}})
    status = funnel.get("status")
    if status not in {"START_AT_CURRENT_TAIL", "PROSPECTIVE_OPERATIONAL_FUNNEL_EVALUATED"}:
        result["reason"] = "CURRENT_FUNNEL_NOT_PUBLISHED"
        result["by_currency_channel"] = []
        return result
    result.update(state="OBSERVED", status=status, as_of=snapshot_time(funnel.get("as_of")),
                  current_as_of=snapshot_time(report.get("as_of")),
                  generation_id=cut["pointer"]["generation_id"],
                  real_orders_sent=report["real_orders_sent"], real_routes=report["real_routes"])
    return result


def timers(projection):
    path = Path(os.getenv("POROTA_SCHEDULER_STATE_PATH", "data/scheduler/systemd_timers.json"))
    payload, source = projection.bounded_file(path.name, root=path.parent)
    result = {"state": "NO_VERIFICADO", "freshness": "NO_VERIFICADO", "timers": []}
    raw_timers = payload.get("timers")
    if (source != "AVAILABLE" or payload.get("schema") != "porota-scheduler-systemd-v1"
            or not isinstance(raw_timers, list) or len(raw_timers) > 128):
        result["reason"] = "SCHEDULER_SNAPSHOT_UNAVAILABLE_OR_UNBOUNDED"
        return result

    # Use the deployed reader's own state/freshness logic on a single fixed sample.
    projection.bounded_file = lambda *args, **kwargs: (payload, source)
    result["as_of"] = snapshot_time(payload.get("recorded_at"))
    result["total_exported"] = len(raw_timers)
    skipped = 0
    for offset in range(0, max(1, len(raw_timers)), 10):
        projection.offset = offset
        page = artifact(projection, "scheduler")
        result["freshness"] = page.state if page.state in {"FRESH", "STALE"} else "NO_VERIFICADO"
        for row in page.rows:
            unit, service = row.get("unit"), row.get("service")
            identifiers = [re.sub(r"[^a-z]", "", name.lower()) for name in (unit, service) if isinstance(name, str)]
            if (not isinstance(unit, str) or not re.fullmatch(r"porota-[A-Za-z0-9_.@-]{1,100}\.timer", unit)
                    or any(alias in identifier for alias in ("ppiwatch", "ppiapiwatch") for identifier in identifiers)):
                skipped += 1
                continue
            clean = {"unit": unit}
            if isinstance(service, str) and re.fullmatch(r"porota-[A-Za-z0-9_.@-]{1,100}\.service", service):
                clean["service"] = service
            states = {
                "state": {"SERVICE_FAILED", "SUSPENDED", "Instantánea publicada", "En ejecución al capturar",
                          "TIMER_ACTIVE", "NO_VERIFICADO"},
                "active": {"active", "inactive", "activating", "deactivating", "failed", "maintenance", "unknown"},
                "enabled": {"enabled", "enabled-runtime", "masked", "masked-runtime", "disabled", "static",
                            "alias", "generated", "transient", "indirect", "linked", "linked-runtime", "unknown"},
                "execution_state": {"RUNNING", "COMPLETED", "NO_EXECUTION_OBSERVED", "UNIT_UNAVAILABLE", "UNKNOWN"},
                "last_result": {"success", "exit-code", "signal", "core-dump", "watchdog", "timeout", "resources",
                                "start-limit-hit", "oom-kill", "protocol", "unknown"},
            }
            for key, allowed in states.items():
                value = row.get(key)
                clean[key] = value if isinstance(value, str) and value in allowed else "NO_VERIFICADO"
            for key in ("last_run", "next_run"):
                clean[key] = snapshot_time(row.get(key))
            for key in ("service_started_at", "service_finished_at"):
                value = systemd_clock(row.get(key))
                clean[key] = value.isoformat() if value else None
            for key in ("exec_main_code", "exec_main_status"):
                value = row.get(key)
                clean[key] = int(value) if type(value) is int and 0 <= value <= 255 else (
                    int(value) if isinstance(value, str) and re.fullmatch(r"\d{1,3}", value) and int(value) <= 255 else None)
            result["timers"].append(clean)
    result["excluded_rows"] = skipped
    if result["freshness"] == "FRESH" and len(result["timers"]) + skipped == len(raw_timers):
        result["state"] = "OBSERVED" if not skipped else "PARTIAL"
    return result


projection = Projection(Store(database_path()))
result = {}
for name, probe in (("funnel", counts_by_stage), ("scheduler", timers)):
    try:
        result[name] = probe(projection)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        result[name] = {"state": "NO_VERIFICADO", "reason": "READ_CONTRACT_REJECTED"}
print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
'''

DASHBOARD_PROBE = r'''
from datetime import datetime, timedelta, timezone
import json
import os
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request
from zoneinfo import ZoneInfo

from rc6_action4_audit import published_audit_is_safe

now = datetime.now(timezone.utc)
art = ZoneInfo("America/Argentina/Buenos_Aires")
day = now.astimezone(art).date()
token = os.getenv("DASHBOARD_ACCESS_TOKEN", "")


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = build_opener(ProxyHandler({}), NoRedirects())


def get(path):
    if not token:
        raise ValueError("LOCAL_DASHBOARD_AUTH_UNAVAILABLE")
    req = Request("http://127.0.0.1:8000" + path, headers={
        "Authorization": "Bearer " + token, "Cache-Control": "no-cache"})
    with opener.open(req, timeout=2) as response:
        data = response.read(524289)
        if response.status != 200 or len(data) > 524288 or response.headers.get_content_type() != "application/json":
            raise ValueError("DASHBOARD_RECEIPT_INVALID")
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("DASHBOARD_RECEIPT_INVALID")
    return payload


def stamp(value, observed):
    if not isinstance(value, str) or len(value) > 48:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo and parsed <= observed else None
    except ValueError:
        return None


def bounded(value):
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def dashboard():
    payload = get("/api/dashboard/truth")
    observed = datetime.now(timezone.utc)
    heartbeat = stamp(payload.get("heartbeat_at"), observed)
    try:
        interval = int(os.getenv("PAPER_OBSERVER_INTERVAL_SECONDS", "60"))
    except ValueError:
        interval = 0
    budget = interval + 15 if 15 <= interval <= 120 else 30
    if (payload.get("projection_schema") != "rc6-dashboard-truth-v1"
            or payload.get("mode") != "PRODUCTION_PAPER" or payload.get("execution") != "SIMULATED"
            or type(payload.get("real_orders_sent")) is not int or payload["real_orders_sent"] != 0
            or heartbeat is None or observed - heartbeat > timedelta(seconds=budget)):
        return {"state": "NO_VERIFICADO", "reason": "DASHBOARD_RUNTIME_IDENTITY_OR_HEARTBEAT_UNVERIFIED"}
    readiness = payload.get("readiness", {})
    if readiness.get("source") != "candidate_identity_v2" or not readiness.get("families"):
        return {"state": "NO_VERIFICADO", "reason": "CANDIDATE_READINESS_NOT_PUBLISHED"}
    families = {"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES",
                "FUTUROS", "CAUCIONES", "FCI", "INDICES", "USA", "DIVISAS"}
    result = {"state": "OBSERVED", "heartbeat_at": heartbeat.isoformat(), "source": "candidate_identity_v2",
              "art_day": day.isoformat(), "real_orders_sent": 0, "families": []}
    open_phase = payload.get("session_state") == "MARKET_OPEN" and payload.get("market_open") is True
    result["market_open"] = open_phase
    result["ready_total"] = bounded(readiness.get("ready"))
    result["candidate_total"] = bounded(readiness.get("total"))
    if not result["candidate_total"]:
        return {"state": "NO_VERIFICADO", "reason": "CANDIDATE_DENOMINATOR_UNAVAILABLE"}
    result["excluded_families"] = 0
    for row in readiness["families"][:64]:
        count = bounded(row.get("candidate_total"))
        ready = bounded(row.get("runtime_ready"))
        if row.get("family") not in families or count is None or ready is None or ready > count:
            result["excluded_families"] += 1
            result["state"] = "PARTIAL"
            continue
        checked_at = stamp(row.get("readiness_as_of"), observed)
        result["families"].append({"family": row["family"], "candidate_total": count, "runtime_ready": ready,
                                   "readiness_as_of": checked_at.isoformat() if checked_at else None,
                                   "latest_row_current_art_day": bool(checked_at and checked_at.astimezone(art).date() == day)})
    if (result["candidate_total"] is None or result["ready_total"] is None or
            sum(row["candidate_total"] for row in result["families"]) != result["candidate_total"] or
            sum(row["runtime_ready"] for row in result["families"]) != result["ready_total"]):
        result["state"] = "PARTIAL"
    result["readiness_scope"] = "CURRENT_CANDIDATE_PROJECTION_NO_SESSION_ROW_FILTER"
    gates = payload.get("strategy_eligibility", {})
    last_gate = stamp(gates.get("as_of"), observed)
    if gates.get("source") == "trade_gate_evaluations" and last_gate and last_gate.astimezone(art).date() == day:
        result["gate_projection"] = {"source": gates["source"], "as_of": last_gate.isoformat(),
            "scope": "DB_TIMESTAMP_DATE_PREFIX_NOT_EXACT_ART_SESSION",
            **{key: bounded(gates.get(key)) for key in ("evaluated_today", "blocked_today", "opened_simulated_today")}}
    else:
        result["gate_projection"] = {"state": "NO_VERIFICADO", "reason": "NO_GATES_WITH_CURRENT_ART_CLOCK"}
    return result


def action4():
    output = {"state": "NO_VERIFICADO", "art_day": day.isoformat()}
    try:
        payload = get("/api/reports/action4")
    except HTTPError as error:
        if error.code != 404:
            raise
        output["reason"] = "DAILY_RECEIPT_NOT_PUBLISHED"
        return output
    observed = datetime.now(timezone.utc)
    if (type(payload.get("schema_version")) is not int or payload["schema_version"] != 1
            or payload.get("status") != "PAPER_READ_ONLY" or not published_audit_is_safe(payload)):
        output["reason"] = "PUBLISHED_DAILY_PAPER_RECEIPT_UNVERIFIED"
        return output
    window = payload.get("window", {})
    daily = payload.get("dashboard_daily_report", {})
    start, end, issued = (stamp(window.get("from_local"), observed), stamp(window.get("to_local"), observed),
                          stamp(payload.get("generated_at_utc"), observed))
    if issued:
        output["published_art_day"] = issued.astimezone(art).date().isoformat()
    if not (start and end and issued and start <= end <= issued and
            start.astimezone(art).date() == end.astimezone(art).date() == issued.astimezone(art).date() == day and
            start.astimezone(art).strftime("%H:%M:%S") == "00:00:00" and daily.get("periodo") == day.isoformat()):
        output["reason"] = "PUBLISHED_RECEIPT_OUTSIDE_CURRENT_ART_DAY"
        return output
    output.update(window_from=start.isoformat(), window_to=end.isoformat(), generated_at_utc=issued.isoformat(),
                  age_seconds=int((observed - end).total_seconds()))
    if observed - end > timedelta(hours=1):
        output["reason"] = "PUBLISHED_DAILY_RECEIPT_STALE"
        return output
    raw = daily.get("gate_final_counts")
    if not isinstance(raw, dict) or len(raw) > 40 or any(bounded(value) is None for value in raw.values()):
        output["reason"] = "PUBLISHED_GATE_COUNTS_UNAVAILABLE"
        return output
    enums = {"APPROVE", "BLOCKED", "BUY", "CLOSE", "ERROR", "HOLD", "NOT_EVALUATED", "OPENED_SIMULATED",
             "REJECT", "REJECTED", "SELL", "SKIPPED", "VETO", "UNKNOWN"}
    allowed = {key: value for key, value in raw.items() if key in enums}
    output.update(state="OBSERVED", scope="PUBLISHED_ALL_FAMILIES_ART_DAY_TO_WINDOW_END",
                  gate_final_counts=allowed, other_count=sum(raw.values()) - sum(allowed.values()))
    return output


result = {}
for name, probe in (("dashboard_readiness", dashboard), ("action4", action4)):
    try:
        result[name] = probe()
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        result[name] = {"state": "NO_VERIFICADO", "reason": "AUTHENTICATED_READ_RECEIPT_UNAVAILABLE"}
print(json.dumps(result, separators=(",", ":")))
'''


def remote_source(watch_source, expected_tree, expected_product):
    return f'''
from datetime import datetime, timezone
import json
import sys
import types

receipt = {{"schema": {SCHEMA!r}, "status": "NO_VERIFICADO", "read_only": True,
            "observed_at": datetime.now(timezone.utc).isoformat()}}
try:
    monitor = types.ModuleType("_rc6_market_watch")
    monitor.__file__ = "rc6_market_watch_readonly.py"
    sys.modules[monitor.__name__] = monitor
    exec(compile({watch_source!r}, monitor.__file__, "exec"), monitor.__dict__)
    context = monitor._rootless_context()
    if (context.current.get("source_tree") != {expected_tree!r}
            or context.current.get("deployment_sha") != {expected_product!r}
            or context.items["observer"]["State"].get("Running") is not True):
        raise ValueError("PRODUCT_BINDING_UNVERIFIED")
    process = context.host.docker.command_result(
        "container", "exec", "-w", "/app", context.roles["observer"], "python", "-B", "-c",
        {CONTAINER_PROBE!r}, timeout=10)
    if process.returncode or len(process.stdout) > 180000:
        raise ValueError("CONTAINER_PROBE_UNAVAILABLE")
    payload = json.loads(process.stdout)
    if set(payload) != {{"funnel", "scheduler"}} or any(not isinstance(row, dict) for row in payload.values()):
        raise ValueError("CONTAINER_RECEIPT_INVALID")
    try:
        if context.items["dashboard"]["State"].get("Running") is not True:
            raise ValueError("DASHBOARD_NOT_RUNNING")
        dashboard = context.host.docker.command_result(
            "container", "exec", "-w", "/app", context.roles["dashboard"], "python", "-B", "-c",
            {DASHBOARD_PROBE!r}, timeout=7)
        if dashboard.returncode or len(dashboard.stdout) > 32000:
            raise ValueError("DASHBOARD_RECEIPT_INVALID")
        public = json.loads(dashboard.stdout)
        if set(public) != {{"dashboard_readiness", "action4"}} or any(not isinstance(row, dict) for row in public.values()):
            raise ValueError("DASHBOARD_RECEIPT_INVALID")
        payload.update(public)
    except Exception:
        for name in ("dashboard_readiness", "action4"):
            payload[name] = {{"state": "NO_VERIFICADO", "reason": "BOUND_DASHBOARD_READ_RECEIPT_UNAVAILABLE"}}
    identity = context.host.identity
    if monitor._read_private_current(identity.state / "current.json", identity.uid) != context.current:
        raise ValueError("RELEASE_CHANGED_DURING_READ")
    states = set(row.get("state") for row in payload.values())
    receipt.update(payload, status="OBSERVED" if states == {{"OBSERVED"}} else (
        "PARTIAL" if states & {{"OBSERVED", "PARTIAL"}} else "NO_VERIFICADO"),
        expected_product_sha={expected_product!r}, source_tree={expected_tree!r},
        source_sha=context.current["source_sha"], real_orders_sent_at_deploy=0,
        host_mutations=0, provider_requests=0, database_opened_by_probe=False, dashboard_gets_only=True)
except Exception as error:
    allowed = {{"PRODUCT_BINDING_UNVERIFIED", "CONTAINER_PROBE_UNAVAILABLE",
                "RELEASE_CHANGED_DURING_READ", "CONTAINER_RECEIPT_INVALID"}}
    receipt["reason"] = str(error) if type(error) is ValueError and str(error) in allowed else "HOST_READ_PROBE_UNAVAILABLE"
print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch-source", type=Path, required=True)
    parser.add_argument("--expected-product", required=True)
    parser.add_argument("--expected-tree", required=True)
    args = parser.parse_args()
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (args.expected_product, args.expected_tree)):
        parser.error("expected immutable GitHub SHA and tree are required")
    from scripts.porota_actions_host import connection, ssh_run

    receipt = {"schema": SCHEMA, "status": "NO_VERIFICADO", "read_only": True}
    try:
        source = remote_source(args.watch_source.read_text(encoding="utf-8"), args.expected_tree, args.expected_product)
        with connection(os.environ) as (ssh, _, _):
            result = ssh_run(ssh, ["timeout", "43", "/usr/bin/python3", "-B", "-"], data=source.encode(), timeout=55)
        if result.returncode or len(result.stdout) > 180000:
            raise ValueError("SSH_RECEIPT_UNAVAILABLE")
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("read_only") is not True:
            raise ValueError("SSH_RECEIPT_INVALID")
        receipt = value
    except (OSError, TypeError, ValueError):
        receipt["reason"] = "SSH_READ_PROBE_UNAVAILABLE"
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    return 0 if receipt.get("status") in {"OBSERVED", "PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
