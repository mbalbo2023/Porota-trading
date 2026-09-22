"""One-time, resumable archive repair through the 2026-09-21 cutoff.

The normal path imports only exact PPI history objects already archived for the
matching identity. It never touches orders, IOL, or other instrument families.
Live PPI gap reads are disabled by default and require a deliberate runtime
override after a counted review. Re-running is idempotent.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

import ak_byma_calendar as byma

CUTOFF = date(2026, 9, 21)
FAMILIES = {"ACCIONES", "CEDEARS"}
LOCK_PATH = Path(os.getenv("HIST_DB_PATH", "/app/data/market_history.db")).parent / ".rc6-history-cutoff-repair.lock"
STATE_TABLE = "rc6_history_cutoff_repair"
RUN_TABLE = "rc6_history_cutoff_repair_runs"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_store():
    from cv_history_store_adapter_hf6 import default_history_store
    return default_history_store()


def _init_state(db) -> None:
    with db.connect() as c:
        c.executescript(f"""
        CREATE TABLE IF NOT EXISTS {STATE_TABLE}(
          cutoff TEXT NOT NULL, symbol TEXT NOT NULL, instrument_type TEXT NOT NULL,
          market TEXT NOT NULL, settlement TEXT NOT NULL, state TEXT NOT NULL,
          source TEXT, requested_from TEXT, requested_to TEXT, valid_rows INTEGER NOT NULL DEFAULT 0,
          latest_date TEXT, error_class TEXT, updated_at TEXT NOT NULL,
          PRIMARY KEY(cutoff,symbol,instrument_type,market,settlement)
        );
        CREATE TABLE IF NOT EXISTS {RUN_TABLE}(
          cutoff TEXT PRIMARY KEY, state TEXT NOT NULL, targets INTEGER NOT NULL,
          archive_imports INTEGER NOT NULL, ppi_queries INTEGER NOT NULL,
          complete INTEGER NOT NULL, failed INTEGER NOT NULL, updated_at TEXT NOT NULL
        );
        """)


def _targets(observer_store) -> list[tuple[str, str, str, str]]:
    with observer_store.connect() as c:
        rows = c.execute(
            """SELECT DISTINCT ticker,instrument_type,market,settlement
               FROM financial_instrument_catalog
               WHERE status='AVAILABLE'
                 AND upper(instrument_type) IN ('ACCIONES','CEDEARS')
                 AND trim(coalesce(ticker,''))<>''"""
        ).fetchall()
    result = set()
    for symbol, family, market, settlement in rows:
        item = (str(symbol or "").strip().upper(), str(family or "").strip().upper(),
                str(market or "").strip().upper(), str(settlement or "").strip().upper())
        if not all(item) or item[1] not in FAMILIES or item[2] == "UNKNOWN" or item[3] == "UNKNOWN":
            continue
        result.add(item)
    return sorted(result)


def _latest(db, identity) -> str | None:
    symbol, family, market, settlement = identity
    with db.connect() as c:
        row = c.execute(
            """SELECT MAX(date) FROM history_canonical_v2
               WHERE symbol=? AND instrument_type=? AND market=? AND settlement=?""",
            identity,
        ).fetchone()
    return str(row[0])[:10] if row and row[0] else None


def _coverage(db, identity) -> dict[str, Any]:
    """Prove continuous BYMA-session coverage from the stored baseline to cutoff."""
    with db.connect() as c:
        rows = c.execute(
            """SELECT DISTINCT substr(date,1,10) FROM history_canonical_v2
               WHERE symbol=? AND instrument_type=? AND market=? AND settlement=?
                 AND date<=? ORDER BY date""",
            (*identity, CUTOFF.isoformat()),
        ).fetchall()
    days = set()
    for row in rows:
        try:
            days.add(date.fromisoformat(str(row[0])[:10]))
        except (TypeError, ValueError):
            continue
    if not days:
        return {"complete": False, "start": None, "latest": None, "expected": 0,
                "present": 0, "missing": 0, "reason": "NO_VALID_HISTORY"}
    start, latest = min(days), max(days)
    expected_days = []
    current = start
    while current <= CUTOFF:
        if byma.es_dia_habil_operativo(current):
            expected_days.append(current)
        current += timedelta(days=1)
    missing = [day for day in expected_days if day not in days]
    complete = latest >= CUTOFF and not missing
    return {"complete": complete, "start": start, "latest": latest,
            "expected": len(expected_days), "present": len(days),
            "missing": len(missing),
            "reason": "COVERAGE_COMPLETE" if complete else
                      ("CUTOFF_NOT_REACHED" if latest < CUTOFF else "MISSING_BYMA_SESSIONS")}


def _save(db, identity, *, state: str, source: str = "", requested_from: str = "",
          rows: int = 0, latest: str | None = None, error: str | None = None) -> None:
    symbol, family, market, settlement = identity
    with db.connect() as c:
        c.execute(
            f"""INSERT INTO {STATE_TABLE}
                (cutoff,symbol,instrument_type,market,settlement,state,source,
                 requested_from,requested_to,valid_rows,latest_date,error_class,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cutoff,symbol,instrument_type,market,settlement) DO UPDATE SET
                 state=excluded.state,source=excluded.source,
                 requested_from=excluded.requested_from,requested_to=excluded.requested_to,
                 valid_rows=excluded.valid_rows,latest_date=excluded.latest_date,
                 error_class=excluded.error_class,updated_at=excluded.updated_at""",
            (CUTOFF.isoformat(), symbol, family, market, settlement, state, source or None,
             requested_from or None, CUTOFF.isoformat(), int(rows), latest, error, _stamp()),
        )


def _archive_payloads(targets: set[tuple[str, str, str, str]]) -> dict[tuple[str, str, str, str], tuple[list[dict], str]]:
    import fb_raw_evidence_exact_v1 as exact

    path = exact.manifest_path()
    if not path.exists():
        return {}
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    selected: dict[tuple[str, str, str, str], tuple[list[dict], str]] = {}
    try:
        rows = c.execute(
            """SELECT recorded_at,inner_sha256,wrapper_json
               FROM exact_attempts_v1
               WHERE origin='PPI_HISTORY' AND quality='VALID_PAYLOAD'
                 AND inner_sha256 IS NOT NULL ORDER BY recorded_at DESC"""
        )
        for row in rows:
            try:
                wrapper = json.loads(row["wrapper_json"] or "{}")
                metadata = wrapper.get("metadata") if isinstance(wrapper.get("metadata"), dict) else {}
                identity = (
                    str(wrapper.get("symbol") or metadata.get("symbol") or "").strip().upper(),
                    str(wrapper.get("instrument_type") or metadata.get("instrument_type") or
                        wrapper.get("asset_class") or metadata.get("asset_class") or "").strip().upper(),
                    str(wrapper.get("market") or metadata.get("market") or metadata.get("exchange") or "").strip().upper(),
                    str(wrapper.get("settlement") or metadata.get("settlement") or "").strip().upper(),
                )
                if identity not in targets or identity in selected:
                    continue
                rebuilt = json.loads(exact.reconstruct(wrapper["wrapper_json"] if "wrapper_json" in wrapper
                                                       else row["wrapper_json"], row["inner_sha256"]))
                try:
                    bars = json.loads(rebuilt.get("payload_json") or "[]")
                except (TypeError, ValueError):
                    continue
                if not isinstance(bars, list):
                    continue
                dates = [str(x.get("date", ""))[:10] for x in bars
                         if isinstance(x, dict) and x.get("date")]
                if dates and max(dates) >= CUTOFF.isoformat():
                    selected[identity] = (bars, str(row["recorded_at"]))
            except (OSError, KeyError, TypeError, ValueError):
                continue
    finally:
        c.close()
    return selected


def _filtered(payload: Any, start: date) -> list[dict]:
    if not isinstance(payload, list):
        return []
    result = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            raw = str(row.get("date") or "")
            day = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            continue
        if start <= day <= CUTOFF:
            result.append(row)
    return result


def _record_exact(observer_store, identity, payload, start: date, attempted_at: str) -> None:
    import bl_candle_engine as canonical_json
    import bf_production_paper_observer as observer
    import fb_raw_evidence_exact_v1 as exact
    import cp_history_ingest_policy_hf6 as policy

    symbol, family, _market, settlement = identity
    metadata = observer.financial_catalog.lookup(observer_store, symbol, family, settlement)
    valid = policy.validate_provider_history(
        _filtered(payload, start), as_of=datetime.fromisoformat(attempted_at.replace("Z", "+00:00")),
        date_from=start, date_to=CUTOFF,
    ).valid_count
    quality = "VALID_PAYLOAD" if valid else "EMPTY_OR_INVALID"
    wrapper = {
        "symbol": symbol, "asset_class": family, "settlement": settlement,
        "date_from": start.isoformat(), "date_to": CUTOFF.isoformat(),
        "metadata": metadata, "valid_rows": valid,
        "payload_json": json.dumps(payload, ensure_ascii=False, default=str),
    }
    row_key = canonical_json.canonical([symbol, family, settlement, attempted_at])
    exact.archive_wrapper(row_key=row_key, wrapper=wrapper, recorded_at=attempted_at, quality=quality)


def _ingest(observer_store, history_store, identity, payload, start: date, *, source: str,
            observed_at: str) -> tuple[int, str | None]:
    import ct_ppi_history_salvage_hf6 as salvage

    bounded = _filtered(payload, start)
    result = salvage.ingest_ppi_payload(
        observer_store, symbol=identity[0], instrument_type=identity[1],
        market=identity[2], settlement=identity[3], payload=bounded,
        requested_from=start, requested_to=CUTOFF, attempted_at=observed_at,
        history_store=history_store,
    )
    return int(result.get("valid_rows") or 0), result.get("last_date")


def _control(db, *, state: str, targets: int, archive: int, ppi: int, complete: int, failed: int) -> None:
    with db.connect() as c:
        c.execute(
            f"""INSERT INTO {RUN_TABLE}
                (cutoff,state,targets,archive_imports,ppi_queries,complete,failed,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(cutoff) DO UPDATE SET state=excluded.state,targets=excluded.targets,
                 archive_imports=excluded.archive_imports,ppi_queries=excluded.ppi_queries,
                 complete=excluded.complete,failed=excluded.failed,updated_at=excluded.updated_at""",
            (CUTOFF.isoformat(), state, targets, archive, ppi, complete, failed, _stamp()),
        )


def main() -> int:
    from cg_paper_workspace import runtime_store
    from bd_ppi_readonly_guard import ProductionMarketReader
    import bf_production_paper_observer as observer

    history_store = _history_store()
    observer_store = runtime_store()
    targets_list = _targets(observer_store)
    if not targets_list:
        print("RC6_HISTORY_CUTOFF_REPAIR=BLOCKED_EMPTY_OR_INVALID_OPERATIONAL_CATALOG")
        return 2
    targets = set(targets_list)
    lock_path = LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("RC6_HISTORY_CUTOFF_REPAIR=BLOCKED_LOCK_HELD")
            return 3
        _init_state(history_store)

        with history_store.connect() as c:
            c.execute(
                f"""INSERT INTO {RUN_TABLE}(cutoff,state,targets,archive_imports,ppi_queries,complete,failed,updated_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(cutoff) DO UPDATE SET
                    state='RUNNING',targets=excluded.targets,updated_at=excluded.updated_at""",
                (CUTOFF.isoformat(), "RUNNING", len(targets), 0, 0, 0, len(targets), _stamp()),
            )

        completed = set()
        archives = _archive_payloads(targets)
        archive_count = ppi_count = failed = 0
        for identity in targets_list:
            latest = _latest(history_store, identity)
            coverage = _coverage(history_store, identity)
            if coverage["complete"]:
                _save(history_store, identity, state="ALREADY_COVERED",
                      source="PPI_CANONICAL", rows=coverage["present"], latest=latest)
                completed.add(identity)
                continue
            archived = archives.get(identity)
            if latest:
                start = date.fromisoformat(latest) + timedelta(days=1)
            elif archived:
                archive_rows = _filtered(archived[0], date.min)
                archive_days = []
                for row in archive_rows:
                    try:
                        archive_days.append(date.fromisoformat(str(row.get("date") or "")[:10]))
                    except ValueError:
                        continue
                start = min(archive_days) if archive_days else None
            else:
                start = None
            if start is None:
                _save(history_store, identity, state="BLOCKED_NO_CANONICAL_BASELINE",
                      source="PPI_ARCHIVE_OR_CANONICAL")
                failed += 1
                continue
            if start > CUTOFF:
                _save(history_store, identity, state="ALREADY_COVERED",
                      source="PPI_CANONICAL", latest=latest)
                completed.add(identity)
                continue
            if archived:
                payload, recorded_at = archived
                try:
                    rows, last = _ingest(observer_store, history_store, identity, payload, start,
                                         source="PPI_ARCHIVE", observed_at=recorded_at)
                    latest = _latest(history_store, identity)
                    coverage = _coverage(history_store, identity)
                    if rows and coverage["complete"]:
                        _save(history_store, identity, state="COMPLETE", source="PPI_ARCHIVE",
                              requested_from=start.isoformat(), rows=coverage["present"], latest=latest)
                        completed.add(identity); archive_count += 1
                        continue
                    _save(history_store, identity, state="ARCHIVE_PARTIAL_COVERAGE",
                          source="PPI_ARCHIVE", requested_from=start.isoformat(),
                          rows=coverage["present"], latest=latest, error=coverage["reason"])
                except Exception as exc:
                    _save(history_store, identity, state="ARCHIVE_REJECTED", source="PPI_ARCHIVE",
                          requested_from=start.isoformat(), error=type(exc).__name__)
            try:
                key, secret = observer._secret()
                reader = ProductionMarketReader(key, secret)
                reader.login_once()
                break
            except Exception as exc:
                _save(history_store, identity, state="PPI_AUTH_FAILED", source="PPI",
                      requested_from=start.isoformat(), error=type(exc).__name__)
                failed += len(targets_list) - len(completed)
                _control(history_store, state="PARTIAL", targets=len(targets), archive=archive_count,
                         ppi=ppi_count, complete=len(completed), failed=failed)
                print(f"RC6_HISTORY_CUTOFF_REPAIR=PARTIAL TARGETS={len(targets)} COMPLETE={len(completed)} AUTH=FAILED")
                return 1
            break
        else:
            reader = None

        if (len(completed) < len(targets_list) and reader is not None
                and os.getenv("RC6_HISTORY_ALLOW_PPI_GAP_REPAIR", "").strip() == "APPROVED"):
            try:
                for index, identity in enumerate(targets_list, 1):
                    if identity in completed:
                        continue
                    latest = _latest(history_store, identity)
                    if not latest:
                        _save(history_store, identity, state="BLOCKED_NO_CANONICAL_BASELINE",
                              source="PPI", error="refusing an unbounded first-history request")
                        failed += 1
                        continue
                    start = date.fromisoformat(latest) + timedelta(days=1)
                    if start > CUTOFF:
                        _save(history_store, identity, state="ALREADY_COVERED",
                              source="PPI_CANONICAL", latest=latest)
                        completed.add(identity)
                        continue
                    attempted_at = observer.now_iso()
                    try:
                        payload = reader.history(identity[0], identity[1], identity[3], start, CUTOFF)
                        _record_exact(observer_store, identity, payload, start, attempted_at)
                        rows, last = _ingest(observer_store, history_store, identity, payload, start,
                                             source="PPI", observed_at=attempted_at)
                        latest = _latest(history_store, identity)
                        if rows:
                            _save(history_store, identity, state="COMPLETE", source="PPI",
                                  requested_from=start.isoformat(), rows=rows, latest=latest)
                            completed.add(identity); ppi_count += 1
                        else:
                            _save(history_store, identity, state="NO_NEW_VALID_ROWS", source="PPI",
                                  requested_from=start.isoformat(), rows=0, latest=latest)
                            failed += 1
                    except Exception as exc:
                        _save(history_store, identity, state="PPI_QUERY_FAILED", source="PPI",
                              requested_from=start.isoformat(), error=type(exc).__name__)
                        failed += 1
                    if index % 20 == 0:
                        _control(history_store, state="RUNNING", targets=len(targets), archive=archive_count,
                                 ppi=ppi_count, complete=len(completed), failed=failed)
                        print(f"RC6_HISTORY_CUTOFF_PROGRESS={index}/{len(targets_list)} COMPLETE={len(completed)} FAILED={failed}", flush=True)
                    time.sleep(max(0.0, float(os.getenv("RC6_HISTORY_REQUEST_PAUSE_SECONDS", "0.4"))))
            finally:
                if reader is not None:
                    reader.close()

        complete = len(completed)
        failed = len(targets_list) - complete
        state = "COMPLETE" if failed == 0 else "PARTIAL"
        _control(history_store, state=state, targets=len(targets), archive=archive_count,
                 ppi=ppi_count, complete=complete, failed=failed)
        print(f"RC6_HISTORY_CUTOFF_REPAIR={state} TARGETS={len(targets)} ARCHIVE={archive_count} PPI={ppi_count} COMPLETE={complete} FAILED={failed}")
        return 0 if state == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
