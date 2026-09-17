"""RC6 structured GDELT event-risk SHADOW collector.

This is NOT the retired generic news feed.  It writes only structured event-risk
evidence into a dedicated store, has no broker/order imports and has no trading
authority.  Network access is GET-only through fk_gdelt_shadow_feed_rc6.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from fk_gdelt_shadow_feed_rc6 import QUERY_PACKS, GDELTShadowError, collect_shadow

DEFAULT_DB = os.environ.get("POROTA_GDELT_EVENT_DB", "/app/data/event_risk/gdelt_shadow_rc6.db")
DEFAULT_SAFETY_DB = os.environ.get("POROTA_PAPER_DB", "/app/data/paper_v17/observer_v17.db")
DEFAULT_MAX_AGE_SECONDS = max(60, int(os.environ.get("POROTA_GDELT_MAX_AGE_SECONDS", "5400")))


class GDELTEventRiskJobError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _connect_safety(path: str):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def assert_paper_safety(path: str = DEFAULT_SAFETY_DB) -> None:
    with _connect_safety(path) as conn:
        row = conn.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    if not row or str(row["mode"]) != "PRODUCTION_PAPER" or int(row["real_orders_sent"] or 0) != 0:
        raise GDELTEventRiskJobError("PAPER_SAFETY_INVARIANT_FAILED")


def _connect_store(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS gdelt_event_risk_runs(
          run_id TEXT PRIMARY KEY,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          state TEXT NOT NULL,
          requested_event_types INTEGER NOT NULL,
          successful_event_types INTEGER NOT NULL DEFAULT 0,
          fetched_events INTEGER NOT NULL DEFAULT 0,
          stored_events INTEGER NOT NULL DEFAULT 0,
          error_count INTEGER NOT NULL DEFAULT 0,
          errors_json TEXT NOT NULL DEFAULT '{}',
          authority TEXT NOT NULL DEFAULT 'SHADOW_ONLY');
        CREATE TABLE IF NOT EXISTS gdelt_event_risk_events(
          event_id TEXT PRIMARY KEY,
          event_type TEXT NOT NULL,
          first_seen_at TEXT NOT NULL,
          published_at TEXT NOT NULL,
          available_to_engine_at TEXT NOT NULL,
          source TEXT NOT NULL,
          source_tier TEXT NOT NULL,
          provenance_url TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          region TEXT NOT NULL,
          confirmed_at TEXT,
          retracted_at TEXT,
          entities_json TEXT NOT NULL,
          exposures_json TEXT NOT NULL,
          first_recorded_at TEXT NOT NULL,
          last_recorded_at TEXT NOT NULL,
          authority TEXT NOT NULL DEFAULT 'SHADOW_ONLY');
        CREATE INDEX IF NOT EXISTS idx_gdelt_event_type_time
          ON gdelt_event_risk_events(event_type,available_to_engine_at);
        """
    )
    return conn


def run_once(*, db_path: str = DEFAULT_DB, safety_db_path: str = DEFAULT_SAFETY_DB,
             event_types=None, timespan: str = "6h", maxrecords: int = 25,
             session=None) -> dict:
    """Fetch bounded structured evidence and persist it with SHADOW_ONLY authority."""
    assert_paper_safety(safety_db_path)
    selected = list(event_types or sorted(QUERY_PACKS))
    unknown = [x for x in selected if x not in QUERY_PACKS]
    if unknown:
        raise GDELTEventRiskJobError("UNKNOWN_EVENT_TYPES:" + ",".join(unknown))
    if not selected:
        raise GDELTEventRiskJobError("NO_EVENT_TYPES")
    maxrecords = int(maxrecords)
    if not 1 <= maxrecords <= 75:
        raise GDELTEventRiskJobError("MAXRECORDS_OUT_OF_RANGE")

    run_id = "GDELT-RC6-" + uuid.uuid4().hex[:20]
    started = _now()
    errors = {}
    successful = fetched = stored = 0
    conn = _connect_store(db_path)
    try:
        conn.execute(
            "INSERT INTO gdelt_event_risk_runs(run_id,started_at,state,requested_event_types) VALUES(?,?,?,?)",
            (run_id, started, "RUNNING", len(selected)),
        )
        conn.commit()
        for event_type in selected:
            try:
                items = collect_shadow(event_type=event_type, timespan=timespan,
                                       maxrecords=maxrecords, session=session)
                successful += 1
                fetched += len(items)
                recorded_at = _now()
                for item in items:
                    event_id = str(item["event_id"])
                    conn.execute(
                        """
                        INSERT INTO gdelt_event_risk_events(
                          event_id,event_type,first_seen_at,published_at,available_to_engine_at,
                          source,source_tier,provenance_url,payload_hash,region,confirmed_at,
                          retracted_at,entities_json,exposures_json,first_recorded_at,last_recorded_at,authority)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'SHADOW_ONLY')
                        ON CONFLICT(event_id) DO UPDATE SET
                          last_recorded_at=excluded.last_recorded_at,
                          confirmed_at=COALESCE(excluded.confirmed_at,gdelt_event_risk_events.confirmed_at),
                          retracted_at=COALESCE(excluded.retracted_at,gdelt_event_risk_events.retracted_at)
                        """,
                        (
                            event_id, item["event_type"], item["first_seen_at"], item["published_at"],
                            item["available_to_engine_at"], item["source"], item["source_tier"],
                            item["provenance_url"], item["payload_hash"], item.get("region") or "GLOBAL",
                            item.get("confirmed_at"), item.get("retracted_at"),
                            json.dumps(item.get("entities") or [], ensure_ascii=False, sort_keys=True),
                            json.dumps(item.get("exposures") or [], ensure_ascii=False, sort_keys=True),
                            recorded_at, recorded_at,
                        ),
                    )
                    stored += 1
                conn.commit()
            except Exception as exc:
                errors[event_type] = f"{type(exc).__name__}:{exc}"
                conn.rollback()
        if successful == len(selected):
            state = "GREEN"
        elif successful:
            state = "AMARILLO_PARTIAL"
        else:
            state = "RED_NO_SOURCE_DATA"
        finished = _now()
        conn.execute(
            """UPDATE gdelt_event_risk_runs SET finished_at=?,state=?,successful_event_types=?,
               fetched_events=?,stored_events=?,error_count=?,errors_json=? WHERE run_id=?""",
            (finished, state, successful, fetched, stored, len(errors),
             json.dumps(errors, ensure_ascii=False, sort_keys=True), run_id),
        )
        conn.commit()
        return {
            "run_id": run_id,
            "state": state,
            "requested_event_types": len(selected),
            "successful_event_types": successful,
            "fetched_events": fetched,
            "stored_events": stored,
            "errors": errors,
            "authority": "SHADOW_ONLY",
            "generic_news_feed": "INTENTIONALLY_OFF_UNTOUCHED",
            "real_order_routes": "NOT_PRESENT",
        }
    finally:
        conn.close()


def latest_status(db_path: str = DEFAULT_DB, *, now: datetime | None = None) -> dict:
    """Read local GDELT evidence and make stale/missing evidence explicit."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise GDELTEventRiskJobError("NOW_TIMESTAMP_NAIVE")
    if not os.path.exists(db_path):
        return {
            "state": "NOT_RUN",
            "last_run_state": "NOT_RUN",
            "freshness": "UNKNOWN",
            "freshness_seconds": None,
            "authority": "SHADOW_ONLY",
        }
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute(
            "SELECT * FROM gdelt_event_risk_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        events = conn.execute(
            "SELECT COUNT(*),MAX(available_to_engine_at) FROM gdelt_event_risk_events"
        ).fetchone()
    out = dict(row) if row else {"state": "NOT_RUN"}
    last_run_state = str(out.get("state") or "NOT_RUN")
    finished = str(out.get("finished_at") or "").strip()
    age = None
    if finished:
        try:
            completed = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            if completed.tzinfo is None:
                raise ValueError("naive")
            age = max(0, int((now.astimezone(timezone.utc) -
                              completed.astimezone(timezone.utc)).total_seconds()))
        except (TypeError, ValueError):
            out["state"] = "INVALID_TIMESTAMP"
            out["freshness"] = "UNKNOWN"
            out["freshness_seconds"] = None
        else:
            out["freshness_seconds"] = age
            out["freshness"] = "FRESH" if age <= DEFAULT_MAX_AGE_SECONDS else "STALE"
            if out["freshness"] == "STALE":
                out["state"] = "STALE"
    else:
        out["freshness"] = "UNKNOWN"
        out["freshness_seconds"] = None
    out["last_run_state"] = last_run_state
    out["events_total"] = int(events[0] or 0)
    out["latest_event_available_at"] = events[1]
    out["authority"] = "SHADOW_ONLY"
    out["decision_effect"] = "OBSERVE_ONLY"
    return out

def main() -> int:
    try:
        result = run_once()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["state"] in {"GREEN", "AMARILLO_PARTIAL"} else 2
    except Exception as exc:
        print(json.dumps({"state": "ERROR", "error": f"{type(exc).__name__}:{exc}",
                          "authority": "SHADOW_ONLY",
                          "generic_news_feed": "INTENTIONALLY_OFF_UNTOUCHED"},
                         ensure_ascii=False, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
