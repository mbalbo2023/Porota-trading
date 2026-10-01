"""Canonical, read-only truth projection for every RC6 dashboard surface.

The dashboard must display each concept from its own authority.  Historical
tables are deliberately absent from readiness/catalog/contract calculations.
This module has no broker clients, order routes or write-capable connection.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SCHEMA = "rc6-dashboard-truth-v1"

AUTHORITY_MATRIX = {
    "runtime": "observer_state",
    "safety": "observer_state + runtime evidence",
    "catalog": "financial_instrument_catalog",
    "readiness": "candidate_identity_v2",
    "contract": "contract_evidence_v2_current",
    "history": "history_canonical_v2 / candle store (historical only)",
    "iol_current": "IOL section cache + source state + last-known-good age",
    "learning_history": "decision_evidence_latest (snapshot at decision time)",
    "scalping": "intraday_scalping_worker_state + candidate/position tables",
    "caucion": "paper_cauciones + allocation/treasury state",
    "timers": "scheduler/systemd evidence",
}

FAMILY_ALIASES = {
    "ACCION": "ACCIONES",
    "CEDEAR": "CEDEARS",
    "ON": "OBLIGACIONES",
    "OBLIGACIONES_NEGOCIABLES": "OBLIGACIONES",
    "ETF": "ETFS",
    "CAUCION": "CAUCIONES",
    "FONDOS_COMUNES": "FCI",
}


def normalize_family(value: Any) -> str:
    raw = str(value or "UNKNOWN").strip().upper().replace("-", "_").replace(" ", "_")
    return FAMILY_ALIASES.get(raw, raw)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _max_stamp(*values: Any) -> str | None:
    clean = [str(value) for value in values if value]
    return max(clean) if clean else None


def _aggregate_normalized_family_rows(rows, numeric_fields):
    """Aggregate aliases after normalization; never use last-write-wins."""
    grouped = {}
    for raw in rows:
        row = dict(raw)
        family = normalize_family(row.get("family"))
        item = grouped.setdefault(
            family,
            {"family": family, **{field: 0 for field in numeric_fields}, "as_of": None},
        )
        for field in numeric_fields:
            item[field] += _int(row.get(field))
        item["as_of"] = _max_stamp(item.get("as_of"), row.get("as_of"))
    return grouped


def normalize_iol_state(raw_state: Any, *, fallback_cache_state: Any = None) -> str:
    """Collapse provider/cache vocabulary into the four operator states."""
    raw = str(raw_state or "").strip().upper()
    fallback = str(fallback_cache_state or "").strip().upper()
    if raw in {"LIVE", "LIVE_FRESH", "READY", "FRESH"}:
        return "LIVE"
    if raw == "CACHE_FRESH":
        return "CACHE_FRESH"
    if raw in {"CACHE_STALE", "STALE"}:
        return "CACHE_STALE"
    if raw in {"SOURCE_UNAVAILABLE", "UNAVAILABLE", "EMPTY_UNEXPECTED", "INSUFFICIENT_DATA", ""}:
        if fallback in {"CACHE_FRESH", "CACHE_STALE"}:
            return fallback
        return "SOURCE_UNAVAILABLE"
    return "SOURCE_UNAVAILABLE"


def iol_truth(*, quote_payload: dict[str, Any] | None = None,
              family_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if quote_payload is None:
        quote_path = Path(os.getenv(
            "POROTA_IOL_SHADOW_CACHE_PATH", "/app/data/market/iol_shadow_latest.json"
        ))
        quote_payload = _read_json(quote_path)
    if family_payload is None:
        # This projection runs in the dashboard container, where host data is
        # mounted at /app/data. The host-side collector intentionally keeps its
        # separate /opt/porota-trading default.
        root = Path(os.getenv("POROTA_IOL_SHADOW_ROOT", "/app/data/market"))
        family_payload = _read_json(root / "iol_family_reference_latest.json")

    quote_rows = [row for row in (quote_payload.get("symbols") or []) if isinstance(row, dict)]
    quote_counts: dict[str, int] = defaultdict(int)
    quote_source_counts: dict[str, int] = defaultdict(int)
    for row in quote_rows:
        source_state = str(row.get("source_state") or row.get("state") or "SOURCE_UNAVAILABLE")
        state = normalize_iol_state(row.get("state"), fallback_cache_state=source_state)
        if source_state.upper() == "SOURCE_UNAVAILABLE" and state == "LIVE":
            state = "SOURCE_UNAVAILABLE"
        quote_counts[state] += 1
        quote_source_counts[source_state.upper()] += 1

    cache_state = normalize_iol_state(family_payload.get("cache_state"))
    section_states = {}
    for section, raw in sorted((family_payload.get("section_states") or {}).items()):
        section_states[str(section)] = {
            "state": normalize_iol_state(raw, fallback_cache_state=cache_state),
            "raw_source_state": str(raw or "SOURCE_UNAVAILABLE").upper(),
            "as_of": (family_payload.get("section_observed_at") or {}).get(section),
        }

    return {
        "source": "IOL cache by section",
        "allowed_states": ["LIVE", "CACHE_FRESH", "CACHE_STALE", "SOURCE_UNAVAILABLE"],
        "quotes": {
            "total": len(quote_rows),
            "states": dict(sorted(quote_counts.items())),
            "source_states": dict(sorted(quote_source_counts.items())),
            "as_of": quote_payload.get("refreshed_at"),
            "state": ("SOURCE_UNAVAILABLE" if not quote_payload else
                      normalize_iol_state(quote_payload.get("cache_state") or
                                          ("LIVE" if quote_rows else "SOURCE_UNAVAILABLE"))),
        },
        "families": {
            "state": cache_state if family_payload else "SOURCE_UNAVAILABLE",
            "sections": section_states,
            "as_of": family_payload.get("refreshed_at") or family_payload.get("last_known_good_at"),
            "last_known_good_at": family_payload.get("last_known_good_at"),
        },
    }


def build(query: Callable[..., list[dict[str, Any]]],
          table: Callable[[str], bool], *,
          quote_payload: dict[str, Any] | None = None,
          family_payload: dict[str, Any] | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    """Build the single dashboard projection using injected read-only helpers."""
    now = now or datetime.now(TZ)
    observer = (query(
        "SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,"
        "heartbeat_at,last_market_data_at,detail FROM observer_state WHERE id=1"
    ) or [{}])[0] if table("observer_state") else {}

    catalog_rows = query(
        """SELECT upper(instrument_type) family,COUNT(*) total,
                  SUM(CASE WHEN upper(status)='AVAILABLE' THEN 1 ELSE 0 END) available,
                  MAX(last_seen_at) as_of
           FROM financial_instrument_catalog GROUP BY upper(instrument_type)
           ORDER BY upper(instrument_type)"""
    ) if table("financial_instrument_catalog") else []

    readiness_rows = query(
        """SELECT upper(instrument_type) family,COUNT(*) total,
                  SUM(CASE WHEN can_simulate=1 AND upper(status)='AVAILABLE' THEN 1 ELSE 0 END) ready,
                  SUM(CASE WHEN can_simulate<>1 OR upper(status)<>'AVAILABLE' THEN 1 ELSE 0 END) paused,
                  MAX(checked_at) as_of
           FROM candidate_identity_v2 GROUP BY upper(instrument_type)
           ORDER BY upper(instrument_type)"""
    ) if table("candidate_identity_v2") else []

    catalog = _aggregate_normalized_family_rows(
        catalog_rows, ("total", "available"),
    )
    readiness = _aggregate_normalized_family_rows(
        readiness_rows, ("total", "ready", "paused"),
    )
    families = []
    for family in sorted(set(catalog) | set(readiness)):
        cat = catalog.get(family, {})
        ready = readiness.get(family, {})
        runtime_ready = _int(ready.get("ready"))
        candidates = _int(ready.get("total"))
        state = ("RUNTIME_READY" if runtime_ready and runtime_ready == candidates else
                 "PARTIAL" if runtime_ready else "PAUSED_EXPLICIT" if candidates else "OBSERVED")
        families.append({
            "family": family,
            "catalog_total": _int(cat.get("total")),
            "catalog_available": _int(cat.get("available")),
            "candidate_total": candidates,
            "runtime_ready": runtime_ready,
            "paused_explicit": _int(ready.get("paused")),
            "state": state,
            "catalog_as_of": cat.get("as_of"),
            "readiness_as_of": ready.get("as_of"),
        })

    contract_rows = query(
        """SELECT upper(family) family,COUNT(*) evidence_rows,
                  COUNT(DISTINCT ticker||'|'||market||'|'||currency||'|'||settlement) identities,
                  COUNT(DISTINCT source_class) sources,MAX(observed_at) as_of
           FROM contract_evidence_v2_current GROUP BY upper(family)
           ORDER BY upper(family)"""
    ) if table("contract_evidence_v2_current") else []
    contract_families = []
    for row in contract_rows:
        item = dict(row)
        item["family"] = normalize_family(item.get("family"))
        contract_families.append(item)

    strategy = {
        "source": "trade_gate_evaluations",
        "state": "EVENT_DRIVEN",
        "eligible_now": None,
        "evaluated_today": 0,
        "opened_simulated_today": 0,
        "blocked_today": 0,
        "as_of": None,
        "detail": "No existe un censo estático de elegibilidad; se observa por evaluación y no se infiere desde READY.",
    }
    if table("trade_gate_evaluations"):
        gate = (query(
            """SELECT COUNT(*) evaluated_today,
                      SUM(CASE WHEN final_result='OPENED_SIMULATED' THEN 1 ELSE 0 END) opened_simulated_today,
                      SUM(CASE WHEN final_result='BLOCKED' THEN 1 ELSE 0 END) blocked_today,
                      MAX(evaluated_at) as_of
               FROM trade_gate_evaluations
               WHERE substr(evaluated_at,1,10)=?""", (now.date().isoformat(),)
        ) or [{}])[0]
        strategy.update({key: _int(gate.get(key)) for key in
                         ("evaluated_today", "opened_simulated_today", "blocked_today")})
        strategy["as_of"] = gate.get("as_of")

    scalping_worker = (query("SELECT * FROM intraday_scalping_worker_state WHERE id=1") or [{}])[0] \
        if table("intraday_scalping_worker_state") else {}
    scalping_candidates = (query(
        "SELECT COUNT(*) total,MAX(evaluated_at) as_of FROM scalping_candidates"
    ) or [{}])[0] if table("scalping_candidates") else {}
    intraday_contracts = (query(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN state='CONFIRMED_INTERVAL_VOLUME' THEN 1 ELSE 0 END) confirmed,
                  MAX(checked_at) as_of FROM ppi_intraday_contract_state"""
    ) or [{}])[0] if table("ppi_intraday_contract_state") else {}
    scalp_fills = (query(
        """SELECT COUNT(*) total,MAX(opened_at) as_of FROM paper_positions
           WHERE features_json LIKE '%SCALPING_PAPER%'"""
    ) or [{}])[0] if table("paper_positions") else {}
    exit_supervisor = (query("SELECT state,heartbeat_at,detail FROM paper_supervisor_state WHERE id=1") or [{}])[0] \
        if table("paper_supervisor_state") else {}
    exit_state = str(exit_supervisor.get("state") or "NOT_STARTED").upper()
    try:
        heartbeat = datetime.fromisoformat(str(exit_supervisor.get("heartbeat_at")).replace("Z", "+00:00"))
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=TZ)
        if not 0 <= (now - heartbeat.astimezone(TZ)).total_seconds() <= 20:
            exit_state = "STALE"
    except (TypeError, ValueError):
        if exit_supervisor:
            exit_state = "UNKNOWN"

    caucion_ledger = (query(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open,
                  SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END) settled,
                  MAX(opened_at) last_attempt,MAX(maturity_at) next_maturity
           FROM paper_cauciones"""
    ) or [{}])[0] if table("paper_cauciones") else {}
    allocation = (query(
        """SELECT evaluated_at,decision_json,paper_id FROM paper_caucion_allocations
           ORDER BY evaluated_at DESC LIMIT 1"""
    ) or [{}])[0] if table("paper_caucion_allocations") else {}
    allocation_decision = _json(allocation.get("decision_json"))
    treasury = (query(
        """SELECT evaluated_at,result_json FROM paper_caucion_treasury_attempts
           ORDER BY evaluated_at DESC LIMIT 1"""
    ) or [{}])[0] if table("paper_caucion_treasury_attempts") else {}
    treasury_result = _json(treasury.get("result_json"))
    selected = allocation_decision.get("selected") if isinstance(allocation_decision.get("selected"), dict) else {}
    allocation_manifest = allocation_decision.get("manifest") if isinstance(allocation_decision.get("manifest"), dict) else {}
    treasury_manifest = treasury_result.get("manifest") if isinstance(treasury_result.get("manifest"), dict) else {}
    manifest = allocation_manifest or treasury_manifest
    offers = manifest.get("offers") if isinstance(manifest.get("offers"), list) else []
    selected_offer = next((offer for offer in offers if isinstance(offer, dict) and
                           offer.get("instrument_id") == selected.get("instrument_id")), {})
    policy = allocation_manifest.get("policy") if isinstance(allocation_manifest.get("policy"), dict) else {}
    window = treasury_manifest.get("window") if isinstance(treasury_manifest.get("window"), dict) else {}

    iol = iol_truth(quote_payload=quote_payload, family_payload=family_payload)
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "authorities": dict(AUTHORITY_MATRIX),
        "runtime": {
            "source": "observer_state",
            "mode": str(observer.get("mode") or "UNKNOWN").upper(),
            "process_state": str(observer.get("process_state") or "UNKNOWN").upper(),
            "session_state": str(observer.get("session_state") or "UNKNOWN").upper(),
            "ppi_auth": str(observer.get("ppi_auth") or "UNKNOWN").upper(),
            "real_orders_sent": _int(observer.get("real_orders_sent")),
            "heartbeat_at": observer.get("heartbeat_at"),
            "last_market_data_at": observer.get("last_market_data_at"),
            "detail": observer.get("detail"),
        },
        "catalog": {
            "source": "financial_instrument_catalog",
            "total": sum(_int(item.get("catalog_total")) for item in families),
            "available": sum(_int(item.get("catalog_available")) for item in families),
            "families": families,
            "as_of": _max_stamp(*(item.get("catalog_as_of") for item in families)),
        },
        "readiness": {
            "source": "candidate_identity_v2",
            "total": sum(_int(item.get("candidate_total")) for item in families),
            "ready": sum(_int(item.get("runtime_ready")) for item in families),
            "paused_explicit": sum(_int(item.get("paused_explicit")) for item in families),
            "families": families,
            "as_of": _max_stamp(*(item.get("readiness_as_of") for item in families)),
        },
        "contract": {
            "source": "contract_evidence_v2_current",
            "evidence_rows": sum(_int(item.get("evidence_rows")) for item in contract_families),
            "identities": sum(_int(item.get("identities")) for item in contract_families),
            "families": contract_families,
            "as_of": _max_stamp(*(item.get("as_of") for item in contract_families)),
        },
        "history": {
            "source": "history_canonical_v2 / candle store",
            "role": "HISTORICAL_ONLY",
            "governs_readiness": False,
        },
        "strategy_eligibility": strategy,
        "iol": iol,
        "scalping": {
            "source": "intraday_scalping_worker_state + candidate/position tables",
            "mode": str(os.getenv("PAPER_SCALPING_MODE", "ACTIVE_OBSERVE")).upper(),
            "worker_state": str(scalping_worker.get("state") or "NOT_STARTED").upper(),
            "heartbeat_at": scalping_worker.get("heartbeat_at"),
            "selected_last_cycle": _int(scalping_worker.get("selected")),
            "successful_last_cycle": _int(scalping_worker.get("successful")),
            "failed_last_cycle": _int(scalping_worker.get("failed")),
            "confirmed_identities_last_cycle": _int(scalping_worker.get("confirmed_identities")),
            "candidate_rows": _int(scalping_candidates.get("total")),
            "candidate_as_of": scalping_candidates.get("as_of"),
            "intraday_contracts": _int(intraday_contracts.get("total")),
            "confirmed_intraday_contracts": _int(intraday_contracts.get("confirmed")),
            "contract_as_of": intraday_contracts.get("as_of"),
            "paper_fills": _int(scalp_fills.get("total")),
            "paper_fills_as_of": scalp_fills.get("as_of"),
            "exit_supervisor_state": exit_state,
            "exit_supervisor_heartbeat_at": exit_supervisor.get("heartbeat_at"),
            "max_hold_minutes": _int(os.getenv("PAPER_SCALPING_MAX_HOLD_MINUTES", "30")),
            "eod_policy": "SUPERVISOR_SESSION_POLICY",
            "real_orders_sent": _int(observer.get("real_orders_sent")),
            "worker_real_orders_sent": _int(scalping_worker.get("real_orders_sent")),
            "detail": scalping_worker.get("detail"),
        },
        "caucion": {
            "source": "paper_cauciones + paper_caucion_allocations + treasury attempts",
            "role": "PAPER_COLOCADORA",
            "ledger_state": "AVAILABLE" if table("paper_cauciones") else "SOURCE_UNAVAILABLE",
            "total": _int(caucion_ledger.get("total")),
            "open": _int(caucion_ledger.get("open")),
            "settled": _int(caucion_ledger.get("settled")),
            "last_attempt": treasury.get("evaluated_at") or allocation.get("evaluated_at") or caucion_ledger.get("last_attempt"),
            "next_maturity": caucion_ledger.get("next_maturity"),
            "allocation_status": allocation_decision.get("status") or treasury_result.get("status") or "NOT_RUN",
            "hold_reason": allocation_decision.get("code") or allocation_decision.get("reason") or
                           treasury_result.get("code") or "NO_ATTEMPT_RECORDED",
            "selected_candidate": allocation_decision.get("instrument_id") or
                                  selected.get("instrument_id"),
            "selected_rate": selected.get("annual_rate_fraction") or selected.get("rate") or
                             selected.get("tna_percent") or selected_offer.get("annual_rate_fraction"),
            "commercial_minimum": selected.get("minimum_principal") or selected.get("min_principal") or
                                  selected_offer.get("minimum_principal") or
                                  "NO_VERIFICADO",
            "paper_policy": treasury_result.get("mode") or "PAPER_COLOCADORA",
            "free_cash": treasury_result.get("current_cash") or treasury_result.get("reference_cash"),
            "reserve": treasury_result.get("reserve_cash"),
            "sweep_budget": treasury_result.get("principal_limit"),
            "window": ((str(window.get("opens_at")) + " → " + str(window.get("closes_at")))
                       if window else "NO_VERIFICADO"),
            "next_liquidity": window.get("liquidity_deadline") or policy.get("liquidity_deadline") or
                              treasury_result.get("liquidity_deadline"),
            "iol_source": (iol.get("families", {}).get("sections", {}).get("caucion:ARS") or
                           {"state": iol.get("families", {}).get("state", "SOURCE_UNAVAILABLE")}),
            "guarantee_required_for_placing_paper": False,
        },
    }


def instrument_rows(query: Callable[..., list[dict[str, Any]]],
                    table: Callable[[str], bool], *, limit: int = 10000,
                    offset: int = 0, q: str = "", family: str = "",
                    market: str = "", currency: str = "",
                    settlement: str = "", state: str = "") -> list[dict[str, Any]]:
    """Bounded full-key catalog/readiness/contract projection for instrument tables.

    LIMIT/OFFSET are applied in SQL so UI pagination never materializes the full
    production catalog merely to hide rows in the browser.
    """
    if not table("financial_instrument_catalog"):
        return []
    readiness_join = table("candidate_identity_v2")
    contract_join = table("contract_evidence_v2_current")
    readiness_columns = (
        "CASE WHEN r.can_simulate=1 AND upper(r.status)='AVAILABLE' THEN 1 ELSE 0 END runtime_ready,"
        "COALESCE(r.status,'NO_CANDIDATE') readiness_status,"
        "COALESCE(r.detail,'CANDIDATE_IDENTITY_NOT_PUBLISHED') readiness_detail,r.checked_at readiness_as_of"
        if readiness_join else
        "0 runtime_ready,'NO_CANDIDATE' readiness_status,'CANDIDATE_IDENTITY_NOT_PUBLISHED' readiness_detail,NULL readiness_as_of"
    )
    contract_columns = (
        "COUNT(DISTINCT e.source_class) contract_sources,MAX(e.observed_at) contract_as_of"
        if contract_join else "0 contract_sources,NULL contract_as_of"
    )
    joins = ""
    if readiness_join:
        joins += """ LEFT JOIN candidate_identity_v2 r ON r.ticker=c.ticker
          AND r.instrument_type=c.instrument_type AND r.market=c.market
          AND r.currency=c.currency AND r.settlement=c.settlement"""
    if contract_join:
        joins += """ LEFT JOIN contract_evidence_v2_current e ON e.ticker=c.ticker
          AND upper(e.family)=upper(c.instrument_type) AND e.market=c.market
          AND e.currency=c.currency AND e.settlement=c.settlement"""
    where = []
    params: list[Any] = []
    if str(q or "").strip():
        needle = "%" + str(q).strip().upper() + "%"
        where.append("(upper(c.ticker) LIKE ? OR upper(c.instrument_type) LIKE ? OR upper(c.market) LIKE ? OR upper(c.currency) LIKE ? OR upper(c.settlement) LIKE ?)")
        params.extend([needle] * 5)
    for column, value in (
        ("c.instrument_type", family), ("c.market", market),
        ("c.currency", currency), ("c.settlement", settlement),
    ):
        if str(value or "").strip():
            where.append(f"upper({column})=upper(?)")
            params.append(str(value).strip())
    if str(state or "").strip() and readiness_join:
        key = str(state).strip().upper()
        if key == "RUNTIME_READY":
            where.append("r.can_simulate=1 AND upper(r.status)='AVAILABLE'")
        elif key == "PAUSED_EXPLICIT":
            where.append("NOT (r.can_simulate=1 AND upper(r.status)='AVAILABLE')")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"""SELECT c.ticker,c.instrument_type,c.market,c.currency,c.settlement,
                     c.status catalog_status,c.capability catalog_capability,c.last_seen_at catalog_as_of,
                     {readiness_columns},{contract_columns}
              FROM financial_instrument_catalog c {joins}{where_sql}
              GROUP BY c.ticker,c.instrument_type,c.market,c.currency,c.settlement
              ORDER BY upper(c.instrument_type),c.ticker,c.market,c.currency,c.settlement
              LIMIT ? OFFSET ?"""
    rows = query(sql, tuple(params) + (
        max(1, min(int(limit), 500)),
        max(0, int(offset)),
    ))
    for row in rows:
        row["family"] = normalize_family(row.get("instrument_type"))
        row["ui_state"] = "RUNTIME_READY" if _int(row.get("runtime_ready")) else "PAUSED_EXPLICIT"
    return rows

def instrument_count(query: Callable[..., list[dict[str, Any]]],
                     table: Callable[[str], bool], *, q: str = "",
                     family: str = "", market: str = "", currency: str = "",
                     settlement: str = "", state: str = "") -> int:
    """Count the same filtered identity space used by instrument_rows."""
    if not table("financial_instrument_catalog"):
        return 0
    readiness_join = table("candidate_identity_v2")
    joins = ""
    if readiness_join:
        joins = """ LEFT JOIN candidate_identity_v2 r ON r.ticker=c.ticker
          AND r.instrument_type=c.instrument_type AND r.market=c.market
          AND r.currency=c.currency AND r.settlement=c.settlement"""
    where = []
    params: list[Any] = []
    if str(q or "").strip():
        needle = "%" + str(q).strip().upper() + "%"
        where.append("(upper(c.ticker) LIKE ? OR upper(c.instrument_type) LIKE ? OR upper(c.market) LIKE ? OR upper(c.currency) LIKE ? OR upper(c.settlement) LIKE ?)")
        params.extend([needle] * 5)
    for column, value in (
        ("c.instrument_type", family), ("c.market", market),
        ("c.currency", currency), ("c.settlement", settlement),
    ):
        if str(value or "").strip():
            where.append(f"upper({column})=upper(?)")
            params.append(str(value).strip())
    if str(state or "").strip() and readiness_join:
        key = str(state).strip().upper()
        if key == "RUNTIME_READY":
            where.append("r.can_simulate=1 AND upper(r.status)='AVAILABLE'")
        elif key == "PAUSED_EXPLICIT":
            where.append("NOT (r.can_simulate=1 AND upper(r.status)='AVAILABLE')")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    row = (query(f"""SELECT COUNT(*) total FROM financial_instrument_catalog c {joins}{where_sql}""",
                 tuple(params)) or [{"total": 0}])[0]
    return _int(row.get("total"))
