"""Persist/read the single specialized CAUCIONES readiness truth for PAPER.

The store contains no broker credentials and cannot route orders. Dashboard,
runtime and deployment checks must consume this same state rather than infer
CAUCIONES readiness from generic READY_PAPER_SPOT catalog counts.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from bs_instrument_contracts import aware_datetime
from rc6_caucion_fresh_data_agent import DEFAULT_EXPECTED_TICKERS, GATE_NAME

STATE_SOURCE = "RC6_CAUCION_SPECIALIZED_READINESS_V1"
MAX_GATE_AGE_SECONDS = 300


class CaucionReadinessStateError(ValueError):
    pass


def init_schema(store) -> None:
    with store.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS caucion_readiness_state(
          id INTEGER PRIMARY KEY CHECK(id=1), evaluated_at TEXT NOT NULL,
          gate_name TEXT NOT NULL, green INTEGER NOT NULL,
          contract_status TEXT NOT NULL, evidence_id TEXT NOT NULL,
          tickers_json TEXT NOT NULL, reasons_json TEXT NOT NULL,
          payload_json TEXT NOT NULL, real_order_capability INTEGER NOT NULL,
          source TEXT NOT NULL)""")


def _validate_gate(gate: Mapping[str, Any]) -> None:
    if not isinstance(gate, Mapping):
        raise CaucionReadinessStateError("gate must be a mapping")
    if gate.get("name") != GATE_NAME or gate.get("family") != "CAUCIONES":
        raise CaucionReadinessStateError("wrong CAUCIONES gate identity")
    if gate.get("real_order_capability") not in (False, 0):
        raise CaucionReadinessStateError("real order capability must be zero")
    if not str(gate.get("evidence_id") or "").strip():
        raise CaucionReadinessStateError("gate evidence_id missing")
    tickers = tuple(sorted(str(x).upper() for x in (gate.get("tickers") or ())))
    expected = tuple(sorted(DEFAULT_EXPECTED_TICKERS))
    if gate.get("green") is True:
        if gate.get("contract_status") != "READY_PAPER_CANDIDATE":
            raise CaucionReadinessStateError("green gate lacks READY_PAPER_CANDIDATE")
        if tickers != expected:
            raise CaucionReadinessStateError("green gate lacks exact expected caucion universe")


def persist_gate(store, gate: Mapping[str, Any], *, evaluated_at, source=STATE_SOURCE) -> dict:
    _validate_gate(gate)
    at = aware_datetime(evaluated_at, "caucion readiness evaluated_at")
    if not str(source or "").strip():
        raise CaucionReadinessStateError("readiness source missing")
    init_schema(store)
    tickers = sorted(str(x).upper() for x in (gate.get("tickers") or ()))
    reasons = list(gate.get("reasons") or ())
    payload = dict(gate)
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("""INSERT OR REPLACE INTO caucion_readiness_state
          VALUES(1,?,?,?,?,?,?,?,?,?,?)""", (
            at.isoformat(), GATE_NAME, int(gate.get("green") is True),
            str(gate.get("contract_status") or "BLOCKED"), str(gate["evidence_id"]),
            json.dumps(tickers, ensure_ascii=False),
            json.dumps(reasons, ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
            0, str(source),
        ))
    return effective_state(store, now=at)


def effective_state(store, *, now, max_age_seconds=MAX_GATE_AGE_SECONDS) -> dict:
    if int(max_age_seconds) <= 0 or int(max_age_seconds) > MAX_GATE_AGE_SECONDS:
        raise CaucionReadinessStateError("readiness max age cannot relax 300s gate TTL")
    init_schema(store)
    with store.connect() as c:
        row = c.execute("SELECT * FROM caucion_readiness_state WHERE id=1").fetchone()
    if not row:
        return {
            "family":"CAUCIONES", "state":"HOLD", "green":False,
            "ready_paper_count":0, "reason":"CAUCION_READINESS_STATE_MISSING",
            "source":STATE_SOURCE, "real_order_capability":False,
        }
    data = dict(row)
    current = aware_datetime(now, "caucion readiness now")
    evaluated = aware_datetime(data["evaluated_at"], "caucion readiness evaluated_at")
    age = (current - evaluated).total_seconds()
    tickers = json.loads(data["tickers_json"] or "[]")
    reasons = json.loads(data["reasons_json"] or "[]")
    stored_green = bool(data["green"])
    stale = age < -5 or age > int(max_age_seconds)
    exact_universe = tuple(sorted(tickers)) == tuple(sorted(DEFAULT_EXPECTED_TICKERS))
    green = (stored_green and not stale and exact_universe
             and data["contract_status"] == "READY_PAPER_CANDIDATE"
             and int(data["real_order_capability"]) == 0)
    reason = ("CAUCION_READINESS_STALE_OR_FUTURE" if stale else
              "CAUCION_UNIVERSE_INCOMPLETE" if stored_green and not exact_universe else
              "GREEN" if green else
              (reasons[0] if reasons else "CAUCION_FRESHNESS_GATE_RED"))
    return {
        "family":"CAUCIONES",
        "state":"READY_PAPER" if green else "HOLD",
        "green":green,
        "ready_paper_count":len(DEFAULT_EXPECTED_TICKERS) if green else 0,
        "evaluated_at":data["evaluated_at"],
        "age_seconds":age,
        "evidence_id":data["evidence_id"],
        "tickers":tickers,
        "reasons":reasons,
        "reason":reason,
        "source":data["source"],
        "real_order_capability":False,
    }
