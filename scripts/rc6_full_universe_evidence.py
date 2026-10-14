#!/usr/bin/env python3
"""Build the RC6 full-universe evidence ledger from read-only sources.

This process never places orders and never changes the execution gate.  It
records an explicit status for every catalog instrument, including missing or
stale evidence, so the dashboard cannot silently turn a gap into READY.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from typing import Any

from rc6_family_readiness import normalize_family
from rc6_dynamic_universe.sources import _canonical_identity
from rc6_shadow_runtime.source_authority import native_time, receipt_time, source_rank
from rc6_source_consolidation import consolidate, _fold_source

ROOT = Path(os.getenv("POROTA_EVIDENCE_ROOT", "/opt/porota-trading/data/market"))
DB = os.getenv("POROTA_OBSERVER_DB", "/opt/porota-trading/data/paper_v17/observer_v17.db")
OUT = Path(os.getenv("POROTA_FULL_EVIDENCE_PATH", str(ROOT / "rc6_instrument_evidence_latest.json")))
SCHEMA = "rc6-full-universe-instrument-evidence-v1"
FRESH_SECONDS = int(os.getenv("POROTA_EVIDENCE_MAX_AGE_SECONDS", "3600"))
PPI_FRESH_SECONDS = int(os.getenv("POROTA_PPI_EVIDENCE_MAX_AGE_SECONDS", "900"))


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def age_state(value: Any, limit: int, current: datetime) -> dict[str, Any]:
    timestamp = iso(value)
    if not timestamp:
        return {"state": "UNKNOWN", "observed_at": None, "age_seconds": None}
    age = (current - datetime.fromisoformat(timestamp)).total_seconds()
    return {
        "state": "FRESH" if 0 <= age <= limit else "STALE",
        "observed_at": timestamp,
        "age_seconds": round(age, 1),
    }


def atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                            prefix=".rc6-evidence-", suffix=".tmp",
                            delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def catalog_rows() -> list[dict[str, Any]]:
    """Read all available identities without writing to the operational DB."""
    rows: list[dict[str, Any]] = []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        conn.execute("PRAGMA query_only=ON")
        tables = {str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "financial_instrument_catalog" not in tables:
            return []
        columns = {str(row[1]) for row in conn.execute(
            "PRAGMA table_info(financial_instrument_catalog)")}
        ticker = "ticker" if "ticker" in columns else "symbol" if "symbol" in columns else None
        family = "instrument_type" if "instrument_type" in columns else "family" if "family" in columns else None
        if not ticker:
            return []
        selected = [ticker] + [name for name in ("id", family, "market", "currency", "settlement", "term", "status", "capability")
                               if name and name in columns and name != ticker]
        status_clause = " WHERE upper(COALESCE(status,'')) IN ('AVAILABLE','ACTIVE','READY')" if "status" in columns else ""
        raw_rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM financial_instrument_catalog{status_clause}"
        ).fetchall()
        positions = {name: index for index, name in enumerate(selected)}
        for raw in raw_rows:
            symbol = str(raw[positions[ticker]] or "").strip().upper()
            if not symbol:
                continue
            item = {"symbol": symbol, "family": normalize_family(raw[positions[family]]) if family else "UNKNOWN"}
            for name in ("market", "currency", "settlement", "term", "id", "capability", "status"):
                if name in positions and raw[positions[name]] is not None:
                    item[name] = raw[positions[name]]
            rows.append(item)
        conn.close()
    except (sqlite3.Error, OSError):
        return []
    finally:
        if conn is not None:
            conn.close()
    # Identity includes currency; conflicting duplicate rows are review evidence.
    unique = {}
    for item in rows:
        key = (item["family"], item["symbol"], str(item.get("market") or "").upper(),
               str(item.get("currency") or "").upper(), str(item.get("settlement") or item.get("term") or "").upper())
        previous = unique.get(key)
        if previous is not None and previous != item:
            previous["review_status"] = "DUPLICATE_CATALOG_IDENTITY_REVIEW_REQUIRED"
            candidates = previous.setdefault("catalog_candidates", [{name: value for name, value in previous.items() if name != "catalog_candidates"}])
            if len(candidates) < 16:
                candidates.append(item)
        else:
            unique[key] = item
    return sorted(unique.values(), key=lambda item: (item["family"], item["symbol"], str(item.get("currency") or "")))


def _exact_key(row):
    try:
        return tuple(_canonical_identity(row))
    except (TypeError, ValueError):
        return None


def _indexed_records(records):
    """Exact identities coexist; legacy scalar references assign no identity."""
    grouped, references = {}, {}
    for raw in records:
        if not isinstance(raw, dict):
            continue
        quote = raw.get("quote") if isinstance(raw.get("quote"), dict) else {}
        row = {**raw, **quote}
        key = _exact_key(row)
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        if not symbol:
            continue
        if key:
            grouped.setdefault(key, []).append(row)
        else:
            references.setdefault(symbol, []).append(row)
    result, symbols = {}, {}
    for key, candidates in sorted(grouped.items()):
        row, conflicts, evidence = _fold_source(candidates)
        row.update(canonical_identity=list(key), identity_binding="EXACT_FIVE_PART_IDENTITY",
                   review_status="CONFLICT_REVIEW_REQUIRED" if conflicts else "NO_CURRENT_CONFLICT",
                   duplicate_conflicts=conflicts, source_candidates=evidence,
                   source_candidate_count=len(candidates))
        result[key] = row
        symbols.setdefault(key[0], []).append(key)
    for symbol, keys in symbols.items():
        if len(keys) == 1 and symbol not in references:
            result[symbol] = result[keys[0]]
        else:
            result[symbol] = {"symbol": symbol, "canonical_identity": None, "identity_binding": "REFERENCE_ONLY_EXACT_IDENTITY_REQUIRED",
                              "review_status": "IDENTITY_AMBIGUOUS_REVIEW_REQUIRED", "identity_candidates": [list(key) for key in keys]}
    for symbol, candidates in references.items():
        row, conflicts, evidence = _fold_source(candidates)
        row.update(canonical_identity=None, identity_binding="REFERENCE_ONLY_EXACT_IDENTITY_REQUIRED",
                   review_status="IDENTITY_AMBIGUOUS_REVIEW_REQUIRED", source_candidates=evidence,
                   duplicate_conflicts=conflicts)
        result.setdefault(symbol, row)
    return result


def primary_rows(current: datetime) -> dict[Any, dict[str, Any]]:
    candidates = [os.getenv("POROTA_PRIMARY_LAST_CACHE_PATH", "").strip(), str(ROOT / "primary_last.json"), "/app/data/market/primary_last.json"]
    for name in candidates:
        if not name:
            continue
        payload = load_json(Path(name))
        values = payload.get("quotes_by_symbol") or payload.get("last_by_symbol")
        if isinstance(values, dict) and values:
            records = []
            captured = receipt_time(payload)
            for symbol, value in values.items():
                row = dict(value) if isinstance(value, dict) else {"last": value}
                row.setdefault("symbol", str(symbol).strip().upper())
                if str(row["symbol"]).strip().upper() != str(symbol).strip().upper():
                    row["ticker"] = str(symbol).strip().upper()
                row.setdefault("captured_at", captured)
                row.setdefault("source", payload.get("source") or "UNVERIFIED_PRIMARY_CACHE_SOURCE")
                if payload.get("market"):
                    row.setdefault("market", payload["market"])
                records.append(row)
            return _indexed_records(records)
    conn = None
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        conn.execute("PRAGMA query_only=ON")
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(market_snapshots)")}
        if not {"symbol", "last"}.issubset(columns):
            return {}
        selected = [name for name in ("id", "source", "symbol", "asset_class", "market", "settlement", "currency",
                                      "bid", "ask", "last", "observed_at", "trade_at", "book_at") if name in columns]
        rows = conn.execute(f"SELECT {', '.join(selected)} FROM market_snapshots ORDER BY {'id' if 'id' in columns else 'rowid'} DESC LIMIT 20000").fetchall()
        positions = {name: index for index, name in enumerate(selected)}
        records, latest = [], {}
        for raw in rows:
            row = {name: raw[positions[name]] for name in selected if name != "id" and raw[positions[name]] is not None}
            if row.get("source") and source_rank(row["source"]) != 0:
                continue
            # Quotes retain their native event and their separate receipt clocks.
            row["source_at"] = row.get("trade_at")
            row["received_at"] = row.get("observed_at")
            row.setdefault("source", "PPI_SQLITE_MARKET_SNAPSHOTS")
            key = _exact_key(row)
            received = row.get("received_at")
            if key and key in latest and received != latest[key]:
                continue
            if key:
                latest[key] = received
            records.append(row)
        return _indexed_records(records)
    except (sqlite3.Error, OSError):
        return {}
    finally:
        if conn is not None:
            conn.close()


def iol_rows() -> dict[Any, dict[str, Any]]:
    payload = load_json(ROOT / "iol_shadow_latest.json")
    return _indexed_records(payload.get("symbols", []) if isinstance(payload.get("symbols"), list) else [])


def byma_rows() -> tuple[dict[Any, dict[str, Any]], dict[str, Any]]:
    payload = load_json(ROOT / "rc6_public_sources_latest.json")
    records = []
    captured = receipt_time(payload)
    errors = []
    for source in payload.get("sources", []) if isinstance(payload.get("sources"), list) else []:
        if not isinstance(source, dict) or str(source.get("source") or "").upper() != "BYMA":
            continue
        received = receipt_time(source) or captured
        errors.extend(source.get("errors", []) if isinstance(source.get("errors"), list) else [])
        for raw in source.get("records", []) if isinstance(source.get("records"), list) else []:
            if isinstance(raw, dict):
                row = dict(raw)
                row.setdefault("captured_at", received)
                records.append(row)
    return _indexed_records(records), {"observed_at": captured, "capture_clock_basis": "AVAILABILITY_ONLY",
        "status": payload.get("schema"), "errors": errors[:100], "provider_available": None, "decision_effect": "OBSERVE_ONLY"}


def numeric_present(row: dict[str, Any]) -> bool:
    fields = ("last", "bid", "ask", "price", "variation_pct", "volume", "cash_volume", "vwap")
    return any(row.get(field) not in (None, "") for field in fields)


def source_view(row: dict[str, Any] | None, source: str, current: datetime, *, expected_identity=None) -> dict[str, Any]:
    row = row or {}
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence = {**row, **quote}
    observed = native_time(evidence)
    limit = PPI_FRESH_SECONDS if source == "PPI" else FRESH_SECONDS
    freshness = age_state(observed, limit, current)
    received = iso(receipt_time(evidence))
    key = _exact_key(evidence)
    expected = tuple(expected_identity) if expected_identity is not None else key
    review = evidence.get("review_status") in {"CONFLICT_REVIEW_REQUIRED", "IDENTITY_AMBIGUOUS_REVIEW_REQUIRED"}
    identity_match = bool(key and key == expected)
    source_state = str(evidence.get("state") or evidence.get("source_state") or "READY").upper()
    source_available = source_state in {"READY", "LIVE_FRESH", "FRESH", "AVAILABLE", "OBSERVE_ONLY"}
    primary_authority = source != "PPI" or source_rank(evidence.get("source")) == 0
    available = bool(received and datetime.fromisoformat(received) <= current and freshness["observed_at"] and datetime.fromisoformat(freshness["observed_at"]) <= datetime.fromisoformat(received))
    eligible = bool(identity_match and not review and available and source_available and primary_authority and freshness["state"] == "FRESH" and numeric_present(evidence))
    return {"present": bool(row), "structured": numeric_present(evidence), "freshness": freshness,
        "capture_observed_at": received, "capture_is_provider_time": False, "identity": list(key) if key else None,
        "identity_binding": "EXACT_FIVE_PART_IDENTITY" if identity_match else "REFERENCE_ONLY_EXACT_IDENTITY_REQUIRED",
        "review_status": evidence.get("review_status", "NO_CURRENT_CONFLICT") if identity_match else "IDENTITY_AMBIGUOUS_REVIEW_REQUIRED",
        "source_state": source_state, "source_path": evidence.get("source_path") or source + ":CACHE",
        "selection_eligible": eligible, "source_authority": "PPI_PRIMARY" if source == "PPI" else "COMPLEMENT_OBSERVE_ONLY",
        "entry_authority": False, "decision_effect": "OBSERVE_ONLY", "provider_available": None,
        "fields": {name: evidence.get(name) for name in ("last", "bid", "ask", "price", "variation_pct", "volume", "cash_volume", "vwap", "currency", "market", "term") if evidence.get(name) not in (None, "")}}


def family_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["family"], []).append(row)
    result = []
    for family in sorted(grouped):
        members = grouped[family]
        result.append({
            "family": family,
            "instrument_count": len(members),
            "paper_shadow_ready": sum(bool(row["paper_auto_enabled"]) for row in members),
            "pending": sum(row["status"] == "PENDING_EVIDENCE" for row in members),
            "blocked": sum(row["status"].startswith("BLOCKED") for row in members),
            "ingestion_attempted": sum(bool(row["ingestion"]["any_source_attempted"]) for row in members),
            "ppi_fresh": sum(row["sources"]["PPI"]["freshness"]["state"] == "FRESH" for row in members),
            "iol_fresh": sum(row["sources"]["IOL"]["freshness"]["state"] == "FRESH" for row in members),
            "byma_fresh": sum(row["sources"]["BYMA"]["freshness"]["state"] == "FRESH" for row in members),
        })
    return result


def build() -> dict[str, Any]:
    current = now()
    catalog = catalog_rows()
    ppi = primary_rows(current)
    iol = iol_rows()
    byma, byma_meta = byma_rows()
    rotation = load_json(ROOT / "iol_shadow_rotation.json")
    iol_progress = load_json(ROOT / "iol_shadow_latest.json").get("progress", {})
    rows = []
    for item in catalog:
        symbol = item["symbol"]
        key = _exact_key(item)
        ppi_row, iol_row, byma_row = ppi.get(key) if key else ppi.get(symbol), iol.get(key) if key else iol.get(symbol), byma.get(key) if key else byma.get(symbol)
        sources = {name: source_view(row, name, current, expected_identity=key or (None,)*5)
                   for name, row in (("PPI", ppi_row), ("IOL", iol_row), ("BYMA", byma_row))}
        joined = consolidate([ppi_row] if ppi_row else [], [iol_row] if iol_row else [], [byma_row] if byma_row else [], as_of=current)
        source_conflict = any(row["review_status"] == "CONFLICT_REVIEW_REQUIRED" for row in joined["rows"])

        available = [name for name, detail in sources.items()
                     if detail["present"] and detail["structured"]]
        fresh = [name for name, detail in sources.items()
                 if detail["selection_eligible"]]
        attempted = bool(available or any(detail["present"] for detail in sources.values()))
        capability = str(item.get("capability") or "UNKNOWN")
        contract_ready = capability.startswith("READY_PAPER_") and key is not None
        if source_conflict or item.get("review_status"):
            status, paper_enabled = "BLOCKED_SOURCE_REVIEW", False
            reason = "SOURCE_OR_CATALOG_CONFLICT_REVIEW_REQUIRED"
        elif contract_ready and "PPI" in fresh and len(available) >= 2:
            status, paper_enabled = "READY_PAPER_SHADOW", True
            reason = "CONTRACT_READY_PPI_PRIMARY_PLUS_COMPLEMENTARY_SOURCE"
        elif contract_ready and "PPI" in fresh:
            status, paper_enabled = "READY_PAPER_PARTIAL_SOURCE", True
            reason = "CONTRACT_READY_WITH_FRESH_SOURCE"
        elif fresh:
            status, paper_enabled = "READY_SHADOW_PARTIAL", False
            reason = "SOURCE_READY_BUT_CONTRACT_OR_EXECUTOR_NOT_READY:" + capability
        else:
            status, paper_enabled = "PENDING_EVIDENCE", False
            reason = "NO_FRESH_STRUCTURED_SOURCE:" + capability
        rows.append({
            "family": item["family"], "symbol": symbol,
            "market": item.get("market") or "UNKNOWN",
            "currency": item.get("currency"),
            "canonical_identity": list(key) if key else None,
            "source_reconciliation": joined,
            "entry_authority": False,
            "settlement": item.get("settlement") or item.get("term") or "",
            "status": status, "paper_auto_enabled": paper_enabled,
            "contract_capability": capability,
            "real_money_authorized": False,
            "sources": sources,
            "ingestion": {
                "catalog_in_scope": True,
                "any_source_attempted": attempted,
                "source_count": len(available),
                "source_names": available,
                "iol_cycle_id": iol_progress.get("cycle_id"),
                "iol_cycle_seen": symbol in set(rotation.get("seen", [])),
                "byma_capture_observed_at": byma_meta.get("observed_at"),
            },
            "reason": reason,
        })
    return {
        "schema": SCHEMA,
        "generated_at": current.isoformat(),
        "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY_BYMA_PUBLIC_SCRAPE",
        "market": "BCBA",
        "universe": {
            "catalog_count": len(catalog),
            "ledger_count": len(rows),
            "ledger_complete": bool(catalog) and len(catalog) == len(rows),
        },
        "cycle": {
            "iol_progress": iol_progress,
            "iol_rotation": rotation,
            "byma_capture": byma_meta,
        },
        "counts": {
            "paper_shadow_ready": sum(row["paper_auto_enabled"] for row in rows),
            "ready_paper_shadow": sum(row["status"] == "READY_PAPER_SHADOW" for row in rows),
            "ready_shadow_partial": sum(row["status"] == "READY_SHADOW_PARTIAL" for row in rows),
            "ready_paper_partial_source": sum(row["status"] == "READY_PAPER_PARTIAL_SOURCE" for row in rows),
            "pending_evidence": sum(row["status"] == "PENDING_EVIDENCE" for row in rows),
            "blocked": sum(row["status"].startswith("BLOCKED") for row in rows),
            "ingestion_attempted": sum(row["ingestion"]["any_source_attempted"] for row in rows),
            "ppi_fresh": sum(row["sources"]["PPI"]["freshness"]["state"] == "FRESH" for row in rows),
            "iol_fresh": sum(row["sources"]["IOL"]["freshness"]["state"] == "FRESH" for row in rows),
            "byma_fresh": sum(row["sources"]["BYMA"]["freshness"]["state"] == "FRESH" for row in rows),
        },
        "families": family_summary(rows),
        "instruments": rows,
        "decision_effect": "OBSERVE_ONLY",
        "paper_shadow_only": True,
        "real_money_authorized": False,
    }


def main() -> int:
    payload = build()
    atomic(OUT, payload)
    print("RC6_FULL_UNIVERSE_EVIDENCE=" + json.dumps({
        "output": str(OUT), "catalog": payload["universe"],
        "counts": payload["counts"], "decision_effect": "OBSERVE_ONLY",
        "real_money_authorized": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
