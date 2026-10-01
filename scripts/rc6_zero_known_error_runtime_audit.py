#!/usr/bin/env python3
"""Read-only postdeploy and stability audit for the unified RC6 candidate."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bm_exit_supervisor import EXIT_SUPERVISOR_HEALTHY_RUNTIME_STATES
from cf_intraday_scalping import SCALPING_HEALTHY_RUNTIME_STATES
from cg_paper_workspace import database_path
from di_caucion_cash_sweep_runtime_hf6 import CASH_SWEEP_HEALTHY_RUNTIME_STATES
from scripts.rc6_dashboard_route_inventory import inventory


BASELINE_READY = {
    "ACCIONES": 126,
    "CEDEARS": 683,
    "BONOS": 1674,
    "LETRAS": 31,
    "OBLIGACIONES": 2041,
    "FCI": 1003,
}
CORE_GET_PATHS = (
    "/", "/vivo", "/trading", "/trading/acciones",
    "/universo-operativo", "/instrumentos", "/validacion", "/analisis",
    "/aprendizaje", "/scalping", "/riesgo", "/historicos", "/reportes",
    "/sistema", "/salud", "/api/dashboard/truth",
)
ALLOWED_IOL_STATES = {"LIVE", "CACHE_FRESH", "CACHE_STALE", "SOURCE_UNAVAILABLE"}
IOL_FALLBACK_ORDER = [
    "IOL_LIVE_BOUNDED_RETRY", "IOL_LAST_KNOWN_GOOD",
    "PPI_PRIMARY", "BYMA_PUBLIC_COMPLEMENTARY",
]
WORKER_MAX_HEARTBEAT_AGE_SECONDS = 300
DEFAULT_HTTP_TIMEOUT_SECONDS = 20
HTML_SURFACE_PATHS = tuple(path for path in CORE_GET_PATHS if not path.startswith("/api/"))
MAX_HTML_BYTES = {
    # Large catalog/live surfaces must stay bounded. The historical failure
    # mode serialized thousands of hidden rows and froze tablet browsers.
    "/instrumentos": 350_000,
    "/universo-operativo": 550_000,
    "/vivo": 300_000,
}
MAX_SERVER_PAGE_RECORDS = {
    "/instrumentos": 10,
    "/universo-operativo": 10,
    # One ten-record page for closes plus one for decisions.
    "/vivo": 20,
}
MAX_PROGRESSIVE_RECORDS = {
    "/instrumentos": 0,
    "/universo-operativo": 0,
    "/vivo": 0,
}
FORBIDDEN_LEGACY_TEXT = (
    "pending contrato",
    "solo Acciones/CEDEARs",
    "sólo Acciones/CEDEARs",
    "DESACTIVADO POR ALCANCE",
)
PATH_HTTP_TIMEOUT_SECONDS = {
    # Runtime evidence after the full contract sweep shows that these pages
    # perform materially heavier read-only aggregation.  The cold cycle was
    # bounded at 14.8s (/), 6.9s (/vivo), 13.5s (/trading), 10.4s
    # (/historicos) and 29.5s (/universo-operativo).  Preserve fail-closed HTTP
    # checks while budgeting post-reconciliation CPU contention explicitly.
    "/": 45,
    "/vivo": 45,
    "/trading": 45,
    "/historicos": 45,
    "/sistema": 45,
    "/salud": 45,
    "/universo-operativo": 60,
}


def _stamp(value):
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("NAIVE_TIMESTAMP")
    return parsed.astimezone(timezone.utc)


def _age_seconds(value):
    return max(0.0, (datetime.now(timezone.utc) - _stamp(value)).total_seconds())


def _http(path):
    token = os.environ.get("DASHBOARD_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DASHBOARD_ACCESS_TOKEN_MISSING")
    request = Request("http://127.0.0.1:8000" + path,
                      headers={"Authorization": "Bearer " + token})
    timeout = PATH_HTTP_TIMEOUT_SECONDS.get(path, DEFAULT_HTTP_TIMEOUT_SECONDS)
    print(f"POROTA_RUNTIME_AUDIT_HTTP_START={path}|TIMEOUT={timeout}", flush=True)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            print(
                f"POROTA_RUNTIME_AUDIT_HTTP_RESULT={path}|HTTP={response.status}|BYTES={len(body)}",
                flush=True,
            )
            return response.status, body
    except HTTPError as exc:
        body = exc.read()
        excerpt = body[:500].decode("utf-8", "replace").replace("\n", " ")
        print(
            f"POROTA_RUNTIME_AUDIT_HTTP_RESULT={path}|HTTP={exc.code}|"
            f"BYTES={len(body)}|BODY={excerpt}",
            flush=True,
        )
        return exc.code, body
    except URLError as exc:
        raise RuntimeError(f"DASHBOARD_HTTP_TRANSPORT_ERROR|path={path}|reason={exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"DASHBOARD_HTTP_TIMEOUT|path={path}|seconds={timeout}") from exc


def _surface_metrics(path, body):
    text = body.decode("utf-8", "replace")
    # Budget/row guards describe serialized DOM records, not JavaScript source
    # literals.  The accessibility helper legitimately contains the historical
    # marker string as code; counting it as an HTML row made server-side paging
    # fail closed after the actual unbounded rows had already been removed.
    markup = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        text,
        flags=re.I | re.S,
    )
    lowered = markup.lower()
    return {
        "bytes": len(body),
        "table_rows": len(re.findall(r"<tr\\b", markup, re.I)),
        "paper_cards": len(re.findall(r"class=['\"][^'\"]*paper-card", markup, re.I)),
        "server_page_records": len(
            re.findall(r"data-porota-server-page-record=['\"]1['\"]", markup, re.I)
        ),
        "progressive_records": len(
            re.findall(r"data-porota-record=['\"]1['\"]", markup, re.I)
        ),
        "literal_pending_badges": len(
            re.findall(r">\\s*(?:PENDING|PENDIENTE)\\s*<", markup, re.I)
        ),
        "mostrar_mas": lowered.count("mostrar más"),
        "legacy_text_hits": [
            phrase for phrase in FORBIDDEN_LEGACY_TEXT if phrase.lower() in lowered
        ],
    }


def _assert_surface_metrics(metrics):
    violations = []
    for path, item in metrics.items():
        byte_limit = MAX_HTML_BYTES.get(path)
        if byte_limit is not None and int(item["bytes"]) > byte_limit:
            violations.append(
                f"{path}:HTML_BYTES={item['bytes']}>{byte_limit}"
            )
        row_limit = MAX_SERVER_PAGE_RECORDS.get(path)
        if row_limit is not None and int(item["server_page_records"]) > row_limit:
            violations.append(
                f"{path}:SERVER_PAGE_RECORDS={item['server_page_records']}>{row_limit}"
            )
        progressive_limit = MAX_PROGRESSIVE_RECORDS.get(path)
        progressive_records = int(item.get("progressive_records", 0) or 0)
        if progressive_limit is not None and progressive_records > progressive_limit:
            violations.append(
                f"{path}:PROGRESSIVE_RECORDS={progressive_records}>{progressive_limit}"
            )
        if item["legacy_text_hits"]:
            violations.append(
                f"{path}:LEGACY_TEXT={','.join(item['legacy_text_hits'])}"
            )
    if violations:
        raise RuntimeError("DASHBOARD_SURFACE_BUDGET_VIOLATION|" + "|".join(violations))


def _iol_snapshot():
    path = Path(os.environ.get(
        "POROTA_IOL_SHADOW_ROOT", "/app/data/market"
    )) / "iol_family_reference_latest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("IOL_REFERENCE_UNAVAILABLE") from exc
    continuation = payload.get("continuation_state")
    if continuation != "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL":
        raise RuntimeError("IOL_CONTINUATION_NOT_FAILSAFE")
    cache_state = str(payload.get("cache_state") or "").upper()
    if cache_state not in {"LIVE_FRESH", "CACHE_FRESH", "CACHE_STALE", "SOURCE_UNAVAILABLE"}:
        raise RuntimeError("IOL_CONTINUATION_STATE_INVALID")
    fallback = payload.get("fallback_order") or []
    if fallback != IOL_FALLBACK_ORDER:
        raise RuntimeError("IOL_FALLBACK_ORDER_INVALID")
    if cache_state in {"LIVE_FRESH", "CACHE_FRESH", "CACHE_STALE"} and not payload.get("last_known_good_at"):
        raise RuntimeError("IOL_LKG_TIMESTAMP_MISSING")
    sections = payload.get("section_states") or {}
    if not isinstance(sections, dict) or not sections:
        raise RuntimeError("IOL_SECTION_STATES_MISSING")
    return {
        "cache_state": cache_state,
        "last_known_good_at": payload.get("last_known_good_at"),
        "refreshed_at": payload.get("refreshed_at"),
        "continuation_state": continuation,
        "fallback_order": fallback,
        "section_states": sections,
    }


def _require_worker_health(name, worker, allowed_states, max_age_seconds=WORKER_MAX_HEARTBEAT_AGE_SECONDS):
    """Fail closed with an actionable worker-specific diagnostic."""
    state = str(worker.get("state") or "UNKNOWN").upper()
    allowed = frozenset(str(value).upper() for value in allowed_states)
    age = float(worker.get("heartbeat_age_seconds"))
    if state not in allowed:
        rendered = ",".join(sorted(allowed))
        raise RuntimeError(
            f"WORKER_STATE_UNHEALTHY|worker={name}|state={state}|allowed={rendered}|"
            f"heartbeat_age_seconds={age:.3f}"
        )
    if not 0 <= age <= max_age_seconds:
        raise RuntimeError(
            f"WORKER_HEARTBEAT_STALE|worker={name}|state={state}|"
            f"heartbeat_age_seconds={age:.3f}|max={max_age_seconds}"
        )


def _candidate_state_violations(connection):
    """Return only unsafe or WS15 residual-classification violations.

    Fail-closed rows such as OBSERVED_SHADOW, STALE, or AVAILABLE with
    can_simulate=0 are legitimate outside the explicit residual families.
    A simulation-ready row, however, must always be AVAILABLE.  Options,
    futures and ON/OBLIGACIONES residuals additionally must be either READY
    or PAUSED_EXPLICIT; no generic third state is accepted there.
    """
    unsafe = int(connection.execute("""
        SELECT COUNT(*) FROM candidate_identity_v2
        WHERE can_simulate NOT IN (0,1)
           OR (can_simulate=1 AND upper(status)<>'AVAILABLE')
           OR (upper(status)='PAUSED_EXPLICIT' AND can_simulate<>0)
    """).fetchone()[0])
    residual = int(connection.execute("""
        SELECT COUNT(*) FROM candidate_identity_v2
        WHERE upper(instrument_type) IN ('OPCIONES','FUTUROS','ON','OBLIGACIONES')
          AND NOT (
              (can_simulate=1 AND upper(status)='AVAILABLE')
              OR (can_simulate=0 AND upper(status)='PAUSED_EXPLICIT')
          )
    """).fetchone()[0])
    return unsafe, residual


def _database_snapshot():
    path = database_path()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("SELECT 1").fetchone()
    observer = dict(connection.execute(
        "SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at "
        "FROM observer_state WHERE id=1"
    ).fetchone() or {})
    rows = [dict(row) for row in connection.execute("""
        SELECT upper(instrument_type) family,
               SUM(CASE WHEN can_simulate=1 AND upper(status)='AVAILABLE' THEN 1 ELSE 0 END) ready,
               SUM(CASE WHEN upper(status)='PAUSED_EXPLICIT' THEN 1 ELSE 0 END) paused,
               COUNT(*) total
        FROM candidate_identity_v2 GROUP BY upper(instrument_type)
        ORDER BY upper(instrument_type)
    """)]
    by_family = {row["family"]: int(row["ready"] or 0) for row in rows}
    ready = sum(by_family.values())
    invalid, residual_violations = _candidate_state_violations(connection)
    generic_pending = int(connection.execute("""
        SELECT COUNT(*) FROM candidate_identity_v2
        WHERE upper(instrument_type) IN ('OPCIONES','FUTUROS','ON','OBLIGACIONES')
          AND (upper(status) LIKE '%PENDING%' OR upper(detail) LIKE '%PENDING%')
    """).fetchone()[0])
    residual = [dict(row) for row in connection.execute("""
        SELECT upper(instrument_type) family,upper(status) status,can_simulate,COUNT(*) count
        FROM candidate_identity_v2
        WHERE upper(instrument_type) IN ('OPCIONES','FUTUROS','ON','OBLIGACIONES')
        GROUP BY upper(instrument_type),upper(status),can_simulate
        ORDER BY family,status,can_simulate
    """)]
    workers = {}
    for name, table in (
        ("scalping", "intraday_scalping_worker_state"),
        ("caucion_cash_sweep", "paper_caucion_cash_sweep_state"),
        ("exit_supervisor", "paper_supervisor_state"),
    ):
        row = dict(connection.execute(f"SELECT * FROM {table} WHERE id=1").fetchone() or {})
        heartbeat_at = row.get("heartbeat_at")
        workers[name] = {
            "state": str(row.get("state") or "UNKNOWN").upper(),
            "heartbeat_at": heartbeat_at,
            "heartbeat_age_seconds": round(_age_seconds(heartbeat_at), 3),
            "real_orders_sent": int(row.get("real_orders_sent") or 0),
            "routes": json.loads(row.get("routes_json") or "[]") if name == "caucion_cash_sweep" else [],
        }
    connection.close()
    return {
        "observer": observer,
        "readiness": {"source": "candidate_identity_v2", "ready": ready,
                      "by_family": by_family, "rows": rows},
        "invalid_candidate_states": invalid,
        "residual_candidate_state_violations": residual_violations,
        "generic_pending_residuals": generic_pending,
        "residual_classification": residual,
        "workers": workers,
    }


def _assert_snapshot(snapshot, baseline=None):
    db = snapshot["database"]
    observer = db["observer"]
    assert observer.get("mode") == "PRODUCTION_PAPER"
    assert int(observer.get("real_orders_sent") or 0) == 0
    if snapshot["iol"]["cache_state"] == "SOURCE_UNAVAILABLE":
        assert str(observer.get("ppi_auth") or "").upper() in {"OK", "AUTHENTICATED"}
    assert _age_seconds(observer.get("heartbeat_at")) <= 300
    readiness = db["readiness"]
    assert readiness["source"] == "candidate_identity_v2"
    assert readiness["ready"] >= 5558
    for family, expected in BASELINE_READY.items():
        assert readiness["by_family"].get(family, 0) >= expected, (family, readiness)
    assert db["invalid_candidate_states"] == 0
    assert db["residual_candidate_state_violations"] == 0
    assert db["generic_pending_residuals"] == 0
    scalping = db["workers"]["scalping"]
    sweep = db["workers"]["caucion_cash_sweep"]
    supervisor = db["workers"]["exit_supervisor"]
    assert os.environ.get("PAPER_SCALPING_MODE", "").upper() == "ACTIVE_PAPER"
    assert os.environ.get("PAPER_CAUCION_SWEEP_MODE", "").upper() == "ACTIVE_PAPER"
    _require_worker_health("scalping", scalping, SCALPING_HEALTHY_RUNTIME_STATES)
    _require_worker_health("caucion_cash_sweep", sweep, CASH_SWEEP_HEALTHY_RUNTIME_STATES)
    _require_worker_health("exit_supervisor", supervisor, EXIT_SUPERVISOR_HEALTHY_RUNTIME_STATES)
    assert sweep["real_orders_sent"] == 0 and sweep["routes"] == []
    truth = snapshot["dashboard_truth"]
    assert truth["mode"] == "PRODUCTION_PAPER" and truth["execution"] == "SIMULATED"
    assert int(truth["real_orders_sent"]) == 0
    assert truth["readiness"]["source"] == "candidate_identity_v2"
    dashboard_ready = int(truth["readiness"]["ready"])
    if dashboard_ready != readiness["ready"]:
        raise RuntimeError(
            "DASHBOARD_READINESS_MISMATCH|"
            f"database_ready={readiness['ready']}|dashboard_ready={dashboard_ready}|"
            f"database_by_family={json.dumps(readiness['by_family'], sort_keys=True, separators=(',', ':'))}|"
            f"dashboard_as_of={truth['readiness'].get('as_of')}"
        )
    assert truth["history"]["governs_readiness"] is False
    assert truth["scalping"]["mode"] == "ACTIVE_PAPER"
    allowed = set(truth["iol"]["allowed_states"])
    assert allowed == ALLOWED_IOL_STATES
    matrix = snapshot["route_inventory"]
    assert matrix["route_count"] == 55 and matrix["registered_paths"] == 53
    assert snapshot["route_matrix_matches_artifact"] is True
    assert all(code == 200 for code in snapshot["http_status"].values())
    _assert_surface_metrics(snapshot.get("surface_metrics", {}))
    if baseline:
        prior = baseline["database"]
        assert readiness["ready"] >= prior["readiness"]["ready"]
        previous_lkg = baseline["iol"].get("last_known_good_at")
        if previous_lkg:
            assert snapshot["iol"].get("last_known_good_at")
        for worker in ("scalping", "caucion_cash_sweep", "exit_supervisor"):
            assert _stamp(db["workers"][worker]["heartbeat_at"]) >= _stamp(
                prior["workers"][worker]["heartbeat_at"])


def collect(label):
    statuses = {}
    bodies = {}
    for path in CORE_GET_PATHS:
        status, body = _http(path)
        statuses[path] = status
        bodies[path] = body
    failures = {path: status for path, status in statuses.items() if status != 200}
    if failures:
        raise RuntimeError(
            "DASHBOARD_HTTP_FAILURES="
            + json.dumps(failures, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    truth = json.loads(bodies["/api/dashboard/truth"].decode("utf-8"))
    surface_metrics = {
        path: _surface_metrics(path, bodies[path]) for path in HTML_SURFACE_PATHS
    }
    _assert_surface_metrics(surface_metrics)
    route_inventory = inventory()
    artifact_matrix = json.loads(Path("DASHBOARD_TRUTH_MATRIX_RC6.json").read_text(encoding="utf-8"))
    return {
        "schema": "porota-rc6-zero-known-error-runtime-audit-v2",
        "label": label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "network_order_test_performed": False,
        "real_routes": [],
        "database": _database_snapshot(),
        "iol": _iol_snapshot(),
        "dashboard_truth": truth,
        "http_status": statuses,
        "surface_metrics": surface_metrics,
        "route_inventory": route_inventory,
        "route_matrix_matches_artifact": route_inventory == artifact_matrix,
        "known_p0": [],
        "known_p1": [],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    snapshot = collect(args.label)
    print("POROTA_RUNTIME_AUDIT_WORKERS=" + json.dumps(
        snapshot["database"]["workers"],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ), flush=True)
    print("POROTA_RUNTIME_AUDIT_READINESS=" + json.dumps({
        "database_ready": snapshot["database"]["readiness"]["ready"],
        "database_by_family": snapshot["database"]["readiness"]["by_family"],
        "dashboard_ready": snapshot["dashboard_truth"]["readiness"].get("ready"),
        "dashboard_total": snapshot["dashboard_truth"]["readiness"].get("total"),
        "dashboard_as_of": snapshot["dashboard_truth"]["readiness"].get("as_of"),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), flush=True)
    _assert_snapshot(snapshot, baseline)
    snapshot["status"] = "GREEN"
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    evidence = {
        "label": args.label,
        "mode": snapshot["database"]["observer"]["mode"],
        "real_orders_sent": int(snapshot["database"]["observer"].get("real_orders_sent") or 0),
        "real_routes": snapshot["real_routes"],
        "readiness_source": snapshot["database"]["readiness"]["source"],
        "ready_total": snapshot["database"]["readiness"]["ready"],
        "ready_by_family": snapshot["database"]["readiness"]["by_family"],
        "invalid_candidate_states": snapshot["database"]["invalid_candidate_states"],
        "generic_pending_residuals": snapshot["database"]["generic_pending_residuals"],
        "iol": snapshot["iol"],
        "workers": snapshot["database"]["workers"],
        "route_count": snapshot["route_inventory"]["route_count"],
        "registered_paths": snapshot["route_inventory"]["registered_paths"],
        "route_matrix_matches_artifact": snapshot["route_matrix_matches_artifact"],
        "http_200": sum(code == 200 for code in snapshot["http_status"].values()),
        "http_checked": len(snapshot["http_status"]),
        "dashboard_surface_metrics": snapshot.get("surface_metrics", {}),
        "read_only": snapshot["read_only"],
        "network_order_test_performed": snapshot["network_order_test_performed"],
    }
    print("POROTA_RUNTIME_AUDIT_EVIDENCE=" + json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    print("POROTA_ZERO_KNOWN_ERROR_RUNTIME_AUDIT=GREEN|" + args.label)
    print("ZERO_KNOWN_P0=YES")
    print("ZERO_KNOWN_P1=YES")
    print("DASHBOARD_TRUTH=CONSISTENT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())