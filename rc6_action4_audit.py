"""Publica una auditoría diaria compacta para el dashboard RC6.

Lee únicamente SQLite y escribe un JSON atómico dentro de los artefactos del
dashboard. No consulta broker, no envía órdenes y no contiene secretos.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import hashlib
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from cg_paper_workspace import artifact_root, database_path, identity_from_connection

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
FILENAME = "rc6_action4_auditoria_latest.json"
WEEKLY_SOURCE_PATH = "/api/reports/action4/source"
WEEKLY_SOURCE_LIMIT = 500


def _weekly_label(value):
    """Preserve exact bounded category labels or fail the source closed."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_. :/-]{1,80}", value):
        raise ValueError("ACTION4_WEEKLY_LABEL_UNVERIFIED")
    return value


def _weekly_clock(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("ACTION4_WEEKLY_SOURCE_CLOCK_UNVERIFIED")
    return stamp


def _build_weekly_source(db_path, *, now=None, max_rows=WEEKLY_SOURCE_LIMIT,
                         seconds=3):
    """Bounded, consistent seven-day PAPER snapshot for the authenticated API.

    The GitHub Action consumes this API, never SQLite.  A cap or query timeout
    produces PARTIAL/RED rather than manufacturing a weekly zero or total.
    """
    now = now or datetime.now(TZ)
    if (now.tzinfo is None or now.utcoffset() is None or
            type(max_rows) is not int or not 0 < max_rows <= WEEKLY_SOURCE_LIMIT or
            not isinstance(seconds, (int, float)) or not 0 < seconds <= 5):
        raise ValueError("ACTION4_WEEKLY_SOURCE_BUDGET_INVALID")
    end = now.astimezone(TZ)
    start = end - timedelta(days=7)
    deadline = time.monotonic() + seconds
    path = Path(db_path).resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.25) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        identity_from_connection(connection)
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        connection.execute("BEGIN")
        state = connection.execute(
            "SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if (state is None or state["mode"] != "PRODUCTION_PAPER" or
                type(state["real_orders_sent"]) is not int or state["real_orders_sent"] != 0):
            raise ValueError("ACTION4_WEEKLY_PAPER_SAFETY_UNVERIFIED")
        columns = {
            table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for table in ("paper_positions", "paper_decisions")
        }
        if (not {"paper_id", "symbol", "asset_class", "currency", "settlement", "status",
                 "opened_at", "closed_at", "close_reason", "net_pnl"} <= columns["paper_positions"] or
                not {"id", "decided_at", "action"} <= columns["paper_decisions"]):
            raise ValueError("ACTION4_WEEKLY_SOURCE_SCHEMA_UNVERIFIED")
        window = (start.isoformat(), end.isoformat(), max_rows + 1)
        closed = [dict(row) for row in connection.execute("""
            SELECT paper_id,symbol,asset_class,currency,settlement,opened_at,
                   closed_at,close_reason,net_pnl
            FROM paper_positions WHERE status='CLOSED'
              AND julianday(closed_at)>=julianday(?)
              AND julianday(closed_at)<julianday(?)
            ORDER BY julianday(closed_at),paper_id LIMIT ?""", window)]
        opened = [dict(row) for row in connection.execute("""
            SELECT paper_id,opened_at FROM paper_positions
            WHERE julianday(opened_at)>=julianday(?)
              AND julianday(opened_at)<julianday(?)
            ORDER BY julianday(opened_at),paper_id LIMIT ?""", window)]
        decisions = [dict(row) for row in connection.execute("""
            SELECT id,decided_at,action FROM paper_decisions
            WHERE julianday(decided_at)>=julianday(?)
              AND julianday(decided_at)<julianday(?)
            ORDER BY julianday(decided_at),id LIMIT ?""", window)]
    complete = all(len(items) <= max_rows for items in (closed, opened, decisions))
    if not complete:
        return {"schema": "RC6_ACTION4_WEEKLY_SOURCE_V1", "status": "PARTIAL",
                "reason": "WINDOW_ROW_BUDGET_EXCEEDED", "max_rows_per_set": max_rows,
                "lower_bound_rows": {"closed": len(closed), "opened": len(opened),
                                     "decisions": len(decisions)},
                "window": {"from_local": start.isoformat(), "to_local": end.isoformat()},
                "mode": state["mode"], "real_orders_sent": state["real_orders_sent"]}
    operations = []
    pnl = {}
    for row in closed:
        _weekly_clock(row["opened_at"])
        _weekly_clock(row["closed_at"])
        currency = str(row["currency"] or "")
        if currency not in {"ARS", "USD", "USD_MEP", "USD_CCL"}:
            return {"schema": "RC6_ACTION4_WEEKLY_SOURCE_V1", "status": "PARTIAL",
                    "reason": "CLOSED_POSITION_CURRENCY_UNVERIFIED",
                    "window": {"from_local": start.isoformat(), "to_local": end.isoformat()},
                    "mode": state["mode"], "real_orders_sent": state["real_orders_sent"]}
        account = pnl.setdefault(currency, {"total": Decimal("0"), "unknown_rows": 0})
        try:
            amount = Decimal(str(row["net_pnl"])) if row["net_pnl"] is not None else None
            if amount is None or not amount.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            amount = None
            account["unknown_rows"] += 1
        if amount is not None:
            account["total"] += amount
        operations.append({"paper_id": _weekly_label(row["paper_id"]),
                           "symbol": _weekly_label(row["symbol"]),
                           "asset_class": _weekly_label(row["asset_class"]),
                           "currency": currency,
                           "settlement": _weekly_label(row["settlement"]),
                           "opened_at": row["opened_at"], "closed_at": row["closed_at"],
                           "close_reason": _weekly_label(row["close_reason"]),
                           "net_pnl": str(amount) if amount is not None else None})
    for row in opened:
        _weekly_clock(row["opened_at"])
    actions = Counter()
    for row in decisions:
        _weekly_clock(row["decided_at"])
        actions[_weekly_label(row["action"])] += 1
    return {"schema": "RC6_ACTION4_WEEKLY_SOURCE_V1", "status": "COMPLETE",
            "source": WEEKLY_SOURCE_PATH,
            "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
            "window": {"from_local": start.isoformat(), "to_local": end.isoformat()},
            "mode": state["mode"], "real_orders_sent": state["real_orders_sent"],
            "max_rows_per_set": max_rows,
            "counts": {"closed": len(closed), "opened": len(opened),
                       "decisions": len(decisions)},
            "operations": operations,
            "decisions_by_action": dict(sorted(actions.items())),
            "pnl_by_currency": {
                key: {"state": "NO_VERIFICADO" if entry["unknown_rows"] else "COMPLETE",
                      "total": None if entry["unknown_rows"] else str(entry["total"]),
                      "unknown_rows": entry["unknown_rows"]}
                for key, entry in sorted(pnl.items())}}


def build_weekly_source(db_path, *, now=None, max_rows=WEEKLY_SOURCE_LIMIT,
                        seconds=3):
    now = now or datetime.now(TZ)
    try:
        return _build_weekly_source(db_path, now=now, max_rows=max_rows,
                                    seconds=seconds)
    except ValueError as exc:
        if str(exc) not in {"ACTION4_WEEKLY_LABEL_UNVERIFIED",
                            "ACTION4_WEEKLY_SOURCE_CLOCK_UNVERIFIED"}:
            raise
        end = now.astimezone(TZ)
        return {"schema": "RC6_ACTION4_WEEKLY_SOURCE_V1", "status": "PARTIAL",
                "reason": str(exc),
                "window": {"from_local": (end - timedelta(days=7)).isoformat(),
                           "to_local": end.isoformat()}}
    except sqlite3.OperationalError as exc:
        if "interrupted" not in str(exc).lower():
            raise
        end = now.astimezone(TZ)
        return {"schema": "RC6_ACTION4_WEEKLY_SOURCE_V1", "status": "PARTIAL",
                "reason": "SQLITE_TIME_BUDGET_EXHAUSTED",
                "window": {"from_local": (end - timedelta(days=7)).isoformat(),
                           "to_local": end.isoformat()}}


def weekly_payload_from_source(source, *, run_id, run_number, workflow_commit,
                               image_label_commit, container_id, image_id):
    """Pure Action consumer: only a complete API receipt may be published."""
    if (not isinstance(source, dict) or source.get("schema") != "RC6_ACTION4_WEEKLY_SOURCE_V1"
            or source.get("status") != "COMPLETE" or source.get("source") != WEEKLY_SOURCE_PATH
            or source.get("mode") != "PRODUCTION_PAPER" or
            type(source.get("real_orders_sent")) is not int or source["real_orders_sent"] != 0):
        raise ValueError("ACTION4_WEEKLY_SOURCE_INCOMPLETE")
    counts = source.get("counts") or {}
    operations = source.get("operations")
    actions = source.get("decisions_by_action")
    if (not isinstance(counts, dict) or not isinstance(operations, list) or
            not isinstance(actions, dict) or counts.get("closed") != len(operations)
            or type(counts.get("opened")) is not int or type(counts.get("decisions")) is not int
            or type(source.get("max_rows_per_set")) is not int or
            not 0 < source["max_rows_per_set"] <= WEEKLY_SOURCE_LIMIT or
            any(type(counts.get(k)) is not int or not 0 <= counts[k] <= source["max_rows_per_set"]
                for k in ("closed", "opened", "decisions"))
            or any(type(v) is not int or v < 0 for v in actions.values())
            or sum(actions.values()) != counts["decisions"]):
        raise ValueError("ACTION4_WEEKLY_SOURCE_COUNTS_INVALID")
    pnl = {}
    try:
        for row in operations:
            if not isinstance(row, dict):
                raise ValueError
            for key in ("paper_id", "symbol", "asset_class", "settlement", "close_reason"):
                _weekly_label(row.get(key))
            currency = row.get("currency")
            if currency not in {"ARS", "USD", "USD_MEP", "USD_CCL"}:
                raise ValueError
            _weekly_clock(row.get("opened_at"))
            _weekly_clock(row.get("closed_at"))
            entry = pnl.setdefault(currency, {"total": Decimal("0"), "unknown_rows": 0})
            if row.get("net_pnl") is None:
                entry["unknown_rows"] += 1
            else:
                amount = Decimal(str(row["net_pnl"]))
                if not amount.is_finite():
                    raise ValueError
                entry["total"] += amount
        expected_pnl = {
            key: {"state": "NO_VERIFICADO" if entry["unknown_rows"] else "COMPLETE",
                  "total": None if entry["unknown_rows"] else str(entry["total"]),
                  "unknown_rows": entry["unknown_rows"]}
            for key, entry in sorted(pnl.items())}
        if expected_pnl != source.get("pnl_by_currency"):
            raise ValueError
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("ACTION4_WEEKLY_SOURCE_PNL_UNVERIFIED") from exc
    encoded = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"scope": "RC6", "status": "SUCCESS", "source": WEEKLY_SOURCE_PATH,
            "database_access_from_action": "NO", "generated_at_utc": source["generated_at_utc"],
            "window": source["window"], "mode": source["mode"],
            "real_orders_sent": source["real_orders_sent"],
            "decision_snapshot_scope": "COMPLETE_PAPER_DECISIONS_TABLE_WINDOW_ONLY",
            "origin": {"workflow": "RC6 Action4 weekly PAPER", "run_id": str(run_id),
                       "run_number": str(run_number),
                       "workflow_commit": str(workflow_commit),
                       "image_label_commit": str(image_label_commit),
                       "container_id": str(container_id), "image_id": str(image_id)},
            "source_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "source_receipt": source,
            "source_counts": counts,
            "scope_evidence": {"positions": "PAPER_POSITIONS_TABLE_ONLY",
                               "decisions": "PAPER_DECISIONS_TABLE_ONLY",
                               "specialized_family_ledgers": "NO_VERIFICADO"},
            "operations": operations,
            "separation": {"executed_closed": counts["closed"],
                           "opened_simulated": counts["opened"],
                           "hold": actions.get("HOLD", 0),
                           "rejected_blocked": sum(value for key, value in actions.items()
                                                   if key.startswith(("BLOCKED", "REJECTED"))),
                           "decision_count": counts["decisions"]},
            "decisions_by_action": actions,
            "pnl_by_currency": source["pnl_by_currency"]}


def weekly_publication_decision(previous, candidate):
    """Monotonic artifact rule; a retry may not replace its own evidence."""
    if not isinstance(previous, dict) or previous.get("status") != "SUCCESS":
        return "STORE"
    old_origin = previous.get("origin") or {}
    new_origin = candidate.get("origin") or {}
    if old_origin.get("run_id") == new_origin.get("run_id"):
        return ("ALREADY_STORED" if previous == candidate
                else "SAME_RUN_EVIDENCE_CHANGED")
    try:
        old_end = _weekly_clock(previous["window"]["to_local"])
        new_end = _weekly_clock(candidate["window"]["to_local"])
    except (KeyError, TypeError, ValueError):
        return "PRIOR_WINDOW_UNVERIFIED"
    return "STALE_WINDOW" if old_end >= new_end else "STORE"


def validate_weekly_payload(payload, now=None):
    """Reject stale or non-PAPER Action 4 receipts before replacing the artifact."""
    now = now or datetime.now(timezone.utc)
    if (not isinstance(payload, dict) or payload.get("scope") != "RC6" or
            payload.get("source") != WEEKLY_SOURCE_PATH or
            payload.get("database_access_from_action") != "NO" or
            payload.get("status") != "SUCCESS" or
            payload.get("mode") != "PRODUCTION_PAPER" or
            type(payload.get("real_orders_sent")) is not int or
            payload["real_orders_sent"] != 0 or
            payload.get("decision_snapshot_scope") != "COMPLETE_PAPER_DECISIONS_TABLE_WINDOW_ONLY" or
            not isinstance(payload.get("operations"), list) or
            not isinstance(payload.get("separation"), dict)):
        raise ValueError("ACTION4_WEEKLY_PAPER_SCOPE_UNVERIFIED")
    evidence = payload.get("scope_evidence") or {}
    if (not isinstance(evidence, dict) or
            evidence.get("positions") != "PAPER_POSITIONS_TABLE_ONLY" or
            evidence.get("decisions") != "PAPER_DECISIONS_TABLE_ONLY" or
            evidence.get("specialized_family_ledgers") != "NO_VERIFICADO"):
        raise ValueError("ACTION4_WEEKLY_SCOPE_EVIDENCE_INVALID")
    counts = payload.get("source_counts") or {}
    separation = payload["separation"]
    actions = payload.get("decisions_by_action") or {}
    if (not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("source_sha256") or "")) or
            not isinstance(counts, dict) or not isinstance(actions, dict) or
            any(type(counts.get(k)) is not int or counts[k] < 0
                for k in ("closed", "opened", "decisions")) or
            len(payload["operations"]) != counts["closed"] or
            any(type(v) is not int or v < 0 for v in actions.values()) or
            sum(actions.values()) != counts["decisions"] or
            any(type(separation.get(k)) is not int for k in
                ("executed_closed", "opened_simulated", "hold", "rejected_blocked", "decision_count")) or
            separation["executed_closed"] != counts["closed"] or
            separation["opened_simulated"] != counts["opened"] or
            separation["decision_count"] != counts["decisions"] or
            separation["hold"] != actions.get("HOLD", 0) or
            separation["rejected_blocked"] != sum(v for k, v in actions.items()
                                                  if k.startswith(("BLOCKED", "REJECTED")))):
        raise ValueError("ACTION4_WEEKLY_COUNTS_UNVERIFIED")
    pnl = payload.get("pnl_by_currency")
    if not isinstance(pnl, dict):
        raise ValueError("ACTION4_WEEKLY_PNL_UNVERIFIED")
    for entry in pnl.values():
        if not isinstance(entry, dict) or type(entry.get("unknown_rows")) is not int:
            raise ValueError("ACTION4_WEEKLY_PNL_UNVERIFIED")
        unknown = entry["unknown_rows"]
        if unknown:
            if entry.get("state") != "NO_VERIFICADO" or entry.get("total") is not None:
                raise ValueError("ACTION4_WEEKLY_PNL_UNVERIFIED")
        else:
            try:
                amount = Decimal(str(entry["total"]))
            except (InvalidOperation, ValueError, TypeError, KeyError):
                raise ValueError("ACTION4_WEEKLY_PNL_UNVERIFIED") from None
            if entry.get("state") != "COMPLETE" or not amount.is_finite():
                raise ValueError("ACTION4_WEEKLY_PNL_UNVERIFIED")
    origin = payload.get("origin") or {}
    if (not isinstance(origin, dict) or
            not str(origin.get("run_id") or "").isdigit() or
            not re.fullmatch(r"[0-9a-f]{40}", str(origin.get("workflow_commit") or "")) or
            not re.fullmatch(r"[0-9a-f]{40}", str(origin.get("image_label_commit") or "")) or
            not re.fullmatch(r"[0-9a-f]{64}", str(origin.get("container_id") or "")) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", str(origin.get("image_id") or ""))):
        raise ValueError("ACTION4_WEEKLY_ORIGIN_UNVERIFIED")
    try:
        derived = weekly_payload_from_source(
            payload["source_receipt"], run_id=origin["run_id"],
            run_number=origin["run_number"], workflow_commit=origin["workflow_commit"],
            image_label_commit=origin["image_label_commit"],
            container_id=origin["container_id"], image_id=origin["image_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ACTION4_WEEKLY_SOURCE_RECEIPT_UNVERIFIED") from exc
    if derived != payload:
        raise ValueError("ACTION4_WEEKLY_SOURCE_RECEIPT_UNVERIFIED")
    window = payload.get("window") or {}
    try:
        start = datetime.fromisoformat(window["from_local"])
        end = datetime.fromisoformat(window["to_local"])
        generated = datetime.fromisoformat(str(payload["generated_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("ACTION4_WEEKLY_WINDOW_UNVERIFIED") from None
    if (any(stamp.tzinfo is None for stamp in (start, end, generated, now)) or
            start.utcoffset() != TZ.utcoffset(start) or
            end.utcoffset() != TZ.utcoffset(end) or
            generated.utcoffset() != timedelta(0) or
            not timedelta(days=6, hours=23) <= end - start <= timedelta(days=7, minutes=1) or
            not timedelta(seconds=-60) <= now - generated <= timedelta(hours=1) or
            not timedelta(seconds=-60) <= generated - end <= timedelta(hours=1)):
        raise ValueError("ACTION4_WEEKLY_WINDOW_UNVERIFIED")
    return payload


def published_audit_is_safe(payload):
    """Historical display authority, distinct from freshness for today's wheel."""
    if not isinstance(payload, dict):
        return False
    if payload.get("status") == "PAPER_READ_ONLY":
        safety = payload.get("safety") or {}
        return (isinstance(safety, dict) and
                safety.get("observer_mode") == "PRODUCTION_PAPER" and
                safety.get("sqlite") == "READ_ONLY" and
                type(safety.get("real_orders_sent")) is int and
                safety["real_orders_sent"] == 0 and
                safety.get("broker_routes_called") is False)
    try:
        stamp = datetime.fromisoformat(str(payload["generated_at_utc"]).replace("Z", "+00:00"))
        validate_weekly_payload(payload, now=stamp)
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _rows(connection, query, params=()):
    # A missing or unreadable required table must fail the audit. Treating a
    # database error as zero activity would manufacture a successful receipt.
    return [dict(row) for row in connection.execute(query, params)]


def _local_day(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(TZ).date().isoformat()
    except (TypeError, ValueError):
        return None


def _columns(connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def build(db_path=None, now=None):
    """Construye un resumen compacto, reproducible y sin escritura en SQLite."""
    now = now or datetime.now(TZ)
    day = now.date().isoformat()
    db_path = Path(db_path or database_path()).resolve()
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        has_session_state = "session_state" in _columns(connection, "observer_state")
        observer = connection.execute(
            "SELECT mode,real_orders_sent" + (",session_state" if has_session_state else "") +
            " FROM observer_state WHERE id=1"
        ).fetchone()
        if (observer is None or observer["mode"] != "PRODUCTION_PAPER" or
                type(observer["real_orders_sent"]) is not int or
                observer["real_orders_sent"] != 0):
            raise ValueError("ACTION4_PAPER_SAFETY_NOT_VERIFIED")
        session_state = observer["session_state"] if has_session_state else None
        position_columns = _columns(connection, "paper_positions")
        has_asset_class = "asset_class" in position_columns
        position_scope = "ALL_PAPER_POSITION_FAMILIES" if has_asset_class else "LEGACY_SCHEMA_UNFILTERED"
        asset_select = ",asset_class" if has_asset_class else ""
        positions = _rows(connection, f"""
            SELECT paper_id,status,opened_at,closed_at,close_reason{asset_select}
            FROM paper_positions
            WHERE (date(opened_at, '-3 hours')=? OR date(closed_at, '-3 hours')=?)""",
            (day, day))
        gates = _rows(connection, """
            SELECT final_result FROM trade_gate_evaluations
            WHERE date(evaluated_at, '-3 hours')=?
        """, (day,))
        events = _rows(connection, """
            SELECT event_type, COUNT(*) AS total
            FROM paper_events
            WHERE date(event_at, '-3 hours')=?
            GROUP BY event_type
            ORDER BY event_type
        """, (day,)) if _columns(connection, "paper_events") else None
        learning = _rows(connection, """
            SELECT outcome, COUNT(*) AS total
            FROM paper_learning_samples
            WHERE date(COALESCE(label_timestamp, feature_timestamp), '-3 hours')=?
            GROUP BY outcome
            ORDER BY outcome
        """, (day,)) if _columns(connection, "paper_learning_samples") else None
    finally:
        connection.close()

    closed = [row for row in positions if row.get("status") == "CLOSED" and _local_day(row.get("closed_at")) == day]
    opened = [row for row in positions if row.get("status") == "OPEN" or _local_day(row.get("opened_at")) == day]
    final = Counter(str(row.get("final_result") or "UNKNOWN") for row in gates)
    reasons = Counter(str(row.get("close_reason") or "UNSPECIFIED") for row in closed)
    events_by_type = ({str(row.get("event_type") or "UNKNOWN"): int(row.get("total") or 0)
                       for row in events} if events is not None else None)
    outcomes = ({str(row.get("outcome") or "UNLABELED"): int(row.get("total") or 0)
                 for row in learning} if learning is not None else None)
    families = Counter(
        str(row.get("asset_class") or "UNKNOWN").upper()
        for row in positions if "asset_class" in row
    )
    lessons = []
    if closed:
        lessons.append(
            f"Cierres simulados del día: {len(closed)}; causas: "
            + (", ".join(f"{name}={count}" for name, count in sorted(reasons.items()))
               or "sin causa informada")
        )
    if final:
        lessons.append(
            "Decisiones finales: "
            + ", ".join(f"{name}={count}" for name, count in sorted(final.items()))
        )
    if outcomes:
        lessons.append(
            "Etiquetas de aprendizaje incorporadas: "
            + ", ".join(f"{name}={count}" for name, count in sorted(outcomes.items()))
        )
    if not lessons:
        lessons.append("Sin actividad PAPER verificable para esta jornada; no se infieren conclusiones.")
    return {
        "schema_version": 1,
        "status": "PAPER_READ_ONLY",
        "generated_at_utc": now.astimezone(ZoneInfo("UTC")).isoformat(),
        "window": {"from_local": f"{day}T00:00:00-03:00", "to_local": now.isoformat()},
        "origin": {
            "job": "action4_audit",
            "run_id": os.getenv("GITHUB_RUN_ID") or "LOCAL_DAILY",
            "commit": os.getenv("GITHUB_SHA") or "NOT_AVAILABLE",
        },
        "separation": {
            "executed_closed": len(closed),
            "opened_simulated": len(opened),
            "hold": final.get("HOLD", 0) + final.get("BLOCKED", 0),
            "buy": final.get("OPENED_SIMULATED", 0),
            "exit_reasons": dict(sorted(reasons.items())),
        },
        "dashboard_daily_report": {
            "periodo": day,
            "closure_state": ("POSTCLOSE_VERIFIED" if session_state == "MARKET_CLOSED"
                              else "NOT_POSTCLOSE"),
            "gate_final_counts": dict(sorted(final.items())),
            "event_counts": dict(sorted(events_by_type.items())) if events_by_type is not None else None,
            "learning_outcomes": dict(sorted(outcomes.items())) if outcomes is not None else None,
            "lessons": lessons,
            "scope": "ALL_CONTRACT_FAMILIES_PAPER",
            "scope_evidence": {
                "positions": position_scope,
                "position_families": dict(sorted(families.items())),
                "gates_events_learning": "LEGACY_ACTIVITY_UNATTRIBUTED_BY_ASSET_CLASS",
                "events": "AVAILABLE" if events is not None else "SOURCE_UNAVAILABLE",
                "learning": "AVAILABLE" if learning is not None else "SOURCE_UNAVAILABLE",
                "interpretation": "Las posiciones PAPER se informan para todas las familias persistidas. Los conteos legacy de gates, eventos y aprendizaje que no guardan familia no se atribuyen retroactivamente.",
            },
        },
        "safety": {
            "sqlite": "READ_ONLY",
            "observer_mode": observer["mode"],
            "observer_session_state": session_state,
            "real_orders_sent": observer["real_orders_sent"],
            "broker_routes_called": False,
        },
    }


def publish(db_path=None, root=None, now=None):
    payload = build(db_path=db_path, now=now)
    target_root = Path(root or artifact_root(db_path or database_path())).resolve() / "reports"
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / FILENAME
    with NamedTemporaryFile("w", encoding="utf-8", dir=target_root, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(target)
    return payload


if __name__ == "__main__":
    result = publish()
    print(f"ACTION4_AUDIT_PUBLISHED={result['dashboard_daily_report']['periodo']}")
