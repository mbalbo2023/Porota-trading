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
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
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
        selected = [ticker] + [name for name in ("id", family, "market", "settlement", "term", "status")
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
            for name in ("market", "settlement", "term", "id"):
                if name in positions and raw[positions[name]] is not None:
                    item[name] = raw[positions[name]]
            rows.append(item)
        conn.close()
    except (sqlite3.Error, OSError):
        return []
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in rows:
        key = (item["family"], item["symbol"], str(item.get("market") or "").upper(),
               str(item.get("settlement") or item.get("term") or "").upper())
        unique[key] = item
    return sorted(unique.values(), key=lambda item: (item["family"], item["symbol"]))


def primary_rows(current: datetime) -> dict[str, dict[str, Any]]:
    candidates = [
        os.getenv("POROTA_PRIMARY_LAST_CACHE_PATH", "").strip(),
        str(ROOT / "primary_last.json"),
        "/app/data/market/primary_last.json",
    ]
    for name in candidates:
        if not name:
            continue
        payload = load_json(Path(name))
        values = payload.get("quotes_by_symbol") or payload.get("last_by_symbol")
        if isinstance(values, dict) and values:
            result = {}
            observed = payload.get("observed_at") or payload.get("refreshed_at") or payload.get("captured_at")
            for symbol, value in values.items():
                row = dict(value) if isinstance(value, dict) else {"last": value}
                row["symbol"] = str(symbol).strip().upper()
                row.setdefault("provider_observed_at", observed)
                result[row["symbol"]] = row
            return result
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
        conn.execute("PRAGMA query_only=ON")
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(market_snapshots)")}
        if not {"symbol", "last"}.issubset(columns):
            return {}
        selected = [name for name in ("id", "symbol", "asset_class", "market", "settlement",
                                      "currency", "bid", "ask", "last", "observed_at", "book_at")
                    if name in columns]
        rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM market_snapshots ORDER BY "
            f"{'id' if 'id' in columns else 'rowid'} DESC LIMIT 20000"
        ).fetchall()
        conn.close()
        positions = {name: index for index, name in enumerate(selected)}
        result = {}
        for raw in rows:
            symbol = str(raw[positions["symbol"]] or "").strip().upper()
            if not symbol or symbol in result:
                continue
            observed = raw[positions["observed_at"]] if "observed_at" in positions else None
            observed = observed or (raw[positions["book_at"]] if "book_at" in positions else None)
            row = {"symbol": symbol, "provider_observed_at": observed}
            for name in selected:
                if name not in {"id", "symbol", "observed_at", "book_at"} and raw[positions[name]] is not None:
                    row[name] = raw[positions[name]]
            result[symbol] = row
        return result
    except (sqlite3.Error, OSError):
        return {}


def iol_rows() -> dict[str, dict[str, Any]]:
    payload = load_json(ROOT / "iol_shadow_latest.json")
    result = {}
    for row in payload.get("symbols", []) if isinstance(payload.get("symbols"), list) else []:
        if isinstance(row, dict) and str(row.get("symbol") or "").strip():
            result[str(row["symbol"]).strip().upper()] = row
    return result


def byma_rows() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    payload = load_json(ROOT / "rc6_public_sources_latest.json")
    result: dict[str, dict[str, Any]] = {}
    observed_at = payload.get("collected_at")
    for source in payload.get("sources", []) if isinstance(payload.get("sources"), list) else []:
        if str(source.get("source") or "").upper() != "BYMA":
            continue
        observed_at = source.get("observed_at") or observed_at
        for row in source.get("records", []) if isinstance(source.get("records"), list) else []:
            if not isinstance(row, dict) or not row.get("symbol"):
                continue
            item = dict(row)
            item.setdefault("provider_observed_at", observed_at)
            result[str(row["symbol"]).strip().upper()] = item
    return result, {"observed_at": observed_at, "status": payload.get("schema")}


def numeric_present(row: dict[str, Any]) -> bool:
    fields = ("last", "bid", "ask", "price", "variation_pct", "volume", "cash_volume", "vwap")
    return any(row.get(field) not in (None, "") for field in fields)


def source_view(row: dict[str, Any] | None, source: str, current: datetime) -> dict[str, Any]:
    row = row or {}
    observed = row.get("provider_observed_at") or row.get("observed_at") or row.get("captured_at") or row.get("timestamp")
    limit = PPI_FRESH_SECONDS if source == "PPI" else FRESH_SECONDS
    freshness = age_state(observed, limit, current)
    return {
        "present": bool(row),
        "structured": numeric_present(row),
        "freshness": freshness,
        "fields": {key: row.get(key) for key in
                   ("last", "bid", "ask", "price", "variation_pct", "volume", "cash_volume",
                    "vwap", "currency", "market", "term") if row.get(key) not in (None, "")},
    }


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
        ppi_row, iol_row, byma_row = ppi.get(symbol), iol.get(symbol), byma.get(symbol)
        sources = {
            "PPI": source_view(ppi_row, "PPI", current),
            "IOL": source_view(iol_row, "IOL", current),
            "BYMA": source_view(byma_row, "BYMA", current),
        }
        available = [name for name, detail in sources.items()
                     if detail["present"] and detail["structured"]]
        fresh = [name for name, detail in sources.items()
                 if detail["freshness"]["state"] == "FRESH"]
        attempted = bool(available or any(detail["present"] for detail in sources.values()))
        if "PPI" in fresh and len(available) >= 2:
            status, paper_enabled = "READY_PAPER_SHADOW", True
            reason = "PPI_PRIMARY_PLUS_COMPLEMENTARY_SOURCE"
        elif fresh:
            status, paper_enabled = "READY_SHADOW_PARTIAL", True
            reason = "OBSERVATION_SOURCE_AVAILABLE_WITH_EXPLICIT_PARTIAL_EVIDENCE"
        else:
            status, paper_enabled = "PENDING_EVIDENCE", False
            reason = "NO_FRESH_STRUCTURED_SOURCE"
        rows.append({
            "family": item["family"], "symbol": symbol,
            "market": item.get("market") or "BCBA",
            "settlement": item.get("settlement") or item.get("term") or "",
            "status": status, "paper_auto_enabled": paper_enabled,
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
