"""Prospective entry hypotheses over one immutable native decision snapshot.

This is a read-only consumer, not a signal or admission authority. Definitions
freeze at the real start watermark. Future books remain a separate outcome
path. The existing same-snapshot engine, labels, metrics and PAPER costs are
reused; neither an experiment nor a label can call a trading callback.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from statistics import stdev
from time import monotonic
from zoneinfo import ZoneInfo

from rc6_performance.common import canonical, digest, identity, number, stamp
from rc6_performance.costs import FeeModel, expected_round_trip_cost, paper_fee_model
from rc6_performance.metrics import auc, distribution
from rc6_performance.shadow import evaluate_same_snapshot, forward_labels, usable_book

SCHEMA = "rc6.runtime-entry-signals.v1"
NATIVE_INPUT_SCHEMA = "rc6.native-entry-signal-input.v1"
REGISTRY_SCHEMA = "rc6.entry-preregistration.v1"
ART = ZoneInfo("America/Argentina/Buenos_Aires")
QUERY_SECONDS = .25
MAX_ROW_BYTES = 128 * 1024
MAX_CHECKPOINT_BYTES = 16 * 1024**2
MAX_ACTIVE = 512
MAX_ARCHIVED = 128
MAX_BOOKS = 96
STRATEGIES = ("SPOT_MOMENTUM_BASELINE", "SCALPING_BASELINE", "futures-dlr-paper-v1")
FAMILIES = ("ACCIONES", "CEDEARS", "ETFS", "FUTUROS")
DEFINITIONS = (
    {"id": "native-baseline", "version": "v1", "kind": "native"},
    {"id": "momentum-volatility", "version": "v1", "kind": "momentum_volatility", "threshold": "0"},
    {"id": "momentum-activity", "version": "v1", "kind": "momentum_activity", "threshold": "0",
     "minimum_rvol": "1"},
    {"id": "microstructure", "version": "v1", "kind": "microstructure", "threshold": "0",
     "maximum_spread_bps": "50"},
    {"id": "time-horizon", "version": "v1", "kind": "time_horizon", "threshold": "0"},
)
READ_COLUMNS = {
    "decision_evidence_snapshots": ("decision_key", "captured_at", "payload_sha256", "payload_json"),
    "market_snapshots": ("source", "metadata_source", "symbol", "asset_class", "settlement", "currency", "market",
        "observed_at", "book_at", "trade_at", "last_kind", "last", "bid", "ask", "bid_size", "ask_size"),
    "paper_fills": ("id", "paper_id", "side", "quantity", "price", "costs", "filled_at", "reason"),
    "paper_family_lifecycle_events": ("event_id", "lifecycle_id", "family",
        "from_state", "to_state", "amount", "occurred_at", "detail_json"),
}
POSITION_COLUMNS = {
    "paper_positions": ("paper_id", "strategy_version", "symbol", "asset_class", "settlement", "currency", "market",
        "status", "quantity", "entry_price", "entry_cost", "opened_at", "closed_at", "exit_price", "exit_cost",
        "gross_pnl", "net_pnl", "close_reason", "features_json"),
    "paper_future_positions": ("lifecycle_id", "symbol", "currency", "market", "settlement", "side", "quantity",
        "cash_multiplier", "entry_price", "settlement_base_price", "last_mark_price", "margin_reserved", "entry_cost",
        "exit_cost", "variation_realized", "unrealized_pnl", "opened_at", "last_mark_at", "last_book_at", "expires_at",
        "status", "closed_at", "close_reason", "metadata_json"),
    "paper_family_lifecycle": ("lifecycle_id", "family", "instrument", "currency", "state", "updated_at", "ledger_total"),
}


def _json(value):
    return json.loads(canonical(value))


def native_entry_snapshot(*, decision_key, quote, action, score, reason, strategy_id,
                          strategy_version, signal_at, decision_at, configuration_fingerprint,
                          entry_signal_inputs, economics=None, git_sha=None):
    """Pure telemetry builder for an existing native writer, never a writer.

    The caller supplies the exact in-memory price vector, quote and native
    clocks used by its unchanged evaluator. No DB read or clock is performed.
    """
    return _json({"schema": "rc6.decision-inputs.v1", "decision_key": decision_key,
        "captured_at": quote.get("observed_at"), "signal_at": signal_at, "decision_at": decision_at,
        "intent_at": None, "entry_fill_committed_at": None,
        "quote_used": quote,
        "decision": {"action": action, "score": score, "reason": reason, "reason_code": reason,
            "technical_gate": "APPROVE" if action in {"BUY", "BUY_CANDIDATE"} else "NOT_CANDIDATE",
            "patrimonial_gate": "NOT_EVALUATED", "final_result": "OBSERVED_NATIVE_SIGNAL", "paper_id": None},
        "runtime": {"strategy_id": strategy_id, "strategy_version": strategy_version, "clock_mode": "NATIVE",
            "signal_at": signal_at, "decision_at": decision_at, "configuration_fingerprint": configuration_fingerprint,
            "git_sha": git_sha, "execution_mode": "PRODUCTION_PAPER_SIMULATED", "real_money_authorized": False},
        "inputs_used": {"entry_signal_inputs": entry_signal_inputs, "economics": economics,
            "candidate": {"action": action, "score": score}, "reason_code": reason}})


def _fingerprint(checkpoint):
    return digest({k: v for k, v in checkpoint.items() if k != "checkpoint_sha256"})


def _position_row(connection, table, column, value):
    """Do not materialize an unbounded mutable JSON/blob before checking it."""
    columns = {r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
    selected, terms = [], []
    for name in POSITION_COLUMNS[table]:
        if name in columns:
            selected.append(f'CASE WHEN length(CAST("{name}" AS BLOB))>{MAX_ROW_BYTES} '
                            f'THEN NULL ELSE "{name}" END AS "{name}"')
            terms.append(f'COALESCE(length(CAST("{name}" AS BLOB)),0)')
        else:
            selected.append(f'NULL AS "{name}"')
    selected.append(f'({"+".join(terms) or "0"}>{MAX_ROW_BYTES}) AS _oversize')
    row = connection.execute(f'SELECT {",".join(selected)} FROM "{table}" WHERE "{column}"=?', (value,)).fetchone()
    if row and row["_oversize"]:
        raise ValueError("PROSPECTIVE_POSITION_ROW_OVERSIZE")
    return {k: v for k, v in dict(row).items() if k != "_oversize"} if row else None


def _read_rows(database, *, as_of, tables, cursors=None, source_key=None, row_limit=200,
               join_positions=False):
    """One bounded readonly WAL transaction; optional native ledger joins.

    Used by the funnel too so all consumers enforce the same source safety.
    A new source takes current tails and never returns historical rows.
    """
    at = stamp(as_of)
    if isinstance(row_limit, bool) or not isinstance(row_limit, int) or not 1 <= row_limit <= 2000:
        raise ValueError("INVALID_PROSPECTIVE_READ_BUDGET")
    path = Path(database).resolve(strict=True)
    stat = path.stat()
    key = digest([str(path), stat.st_dev, stat.st_ino])
    c = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.005)
    c.row_factory = sqlite3.Row
    deadline = monotonic() + QUERY_SECONDS
    try:
        c.execute("PRAGMA query_only=ON")
        c.execute("PRAGMA busy_timeout=5")
        c.set_progress_handler(lambda: int(monotonic() > deadline), 200)
        if c.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise ValueError("PROSPECTIVE_SOURCE_WAL_REQUIRED")
        c.execute("BEGIN")
        state = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not state or tuple(state) != ("PRODUCTION_PAPER", 0):
            raise ValueError("PROSPECTIVE_PAPER_SAFETY_REQUIRED")
        available = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1000")}
        if "decision_evidence_snapshots" not in available:
            raise ValueError("IMMUTABLE_DECISION_SOURCE_REQUIRED")
        missing = sorted(set(tables) - available)
        tails = {table: c.execute(f'SELECT COALESCE(MAX(rowid),0) FROM "{table}"').fetchone()[0]
                 if table in available else 0 for table in tables}
        bootstrap = source_key != key or cursors is None or any(tails[t] < cursors.get(t, 0) for t in tables)
        if bootstrap:
            return {"source_key": key, "tails": tails, "rows": {}, "positions": {}, "futures": {}, "future_lifecycles": {},
                    "bootstrap": True, "truncated": False, "missing_tables": missing}
        rows, truncated = {}, False
        for table in tables:
            rows[table] = []
            if table in missing:
                continue
            columns = {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}
            names = READ_COLUMNS[table]
            terms = [f'COALESCE(length(CAST("{name}" AS BLOB)),0)' for name in names if name in columns]
            selected = ["rowid AS _rowid"] + [
                (f'CASE WHEN length(CAST("{name}" AS BLOB))>{MAX_ROW_BYTES} THEN NULL ELSE "{name}" END AS "{name}"'
                 if name in columns else f'NULL AS "{name}"') for name in names]
            selected.append(f'({"+".join(terms) or "0"}>{MAX_ROW_BYTES}) AS _oversize')
            batch = list(c.execute(f'SELECT {",".join(selected)} FROM "{table}" WHERE rowid>? ORDER BY rowid LIMIT ?',
                                   (cursors.get(table, 0), row_limit + 1)))
            rows[table] = [dict(r) for r in batch[:row_limit]]
            truncated |= len(batch) > row_limit
        positions, futures, future_lifecycles = {}, {}, {}
        if join_positions:
            for fill in rows.get("paper_fills", []):
                paper_id = fill.get("paper_id")
                if paper_id and paper_id not in positions and "paper_positions" in available:
                    row = _position_row(c, "paper_positions", "paper_id", paper_id)
                    if row:
                        value = dict(row)
                        if len(canonical(value).encode()) > MAX_ROW_BYTES:
                            raise ValueError("PROSPECTIVE_POSITION_ROW_OVERSIZE")
                        positions[paper_id] = value
            for event in rows.get("paper_family_lifecycle_events", []):
                life = event.get("lifecycle_id")
                if life and life not in futures and "paper_future_positions" in available:
                    row = _position_row(c, "paper_future_positions", "lifecycle_id", life)
                    if row:
                        value = dict(row)
                        if len(canonical(value).encode()) > MAX_ROW_BYTES:
                            raise ValueError("PROSPECTIVE_FUTURES_ROW_OVERSIZE")
                        futures[life] = value
                    if "paper_family_lifecycle" in available:
                        ledger = _position_row(c, "paper_family_lifecycle", "lifecycle_id", life)
                        if ledger:
                            future_lifecycles[life] = ledger
        return {"source_key": key, "tails": tails, "rows": rows, "positions": positions, "futures": futures,
                "future_lifecycles": future_lifecycles,
                "bootstrap": False, "truncated": truncated, "missing_tables": missing}
    finally:
        c.close()


def _native_payload(row, at, *, require_signal=True):
    if row.get("_oversize"):
        raise ValueError("IMMUTABLE_DECISION_ROW_OVERSIZE")
    raw = row.get("payload_json")
    if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != row.get("payload_sha256"):
        raise ValueError("IMMUTABLE_DECISION_HASH_MISMATCH")
    payload = json.loads(raw)
    if payload.get("decision_key") != row.get("decision_key"):
        raise ValueError("IMMUTABLE_DECISION_KEY_MISMATCH")
    runtime = payload.get("runtime") or {}
    if runtime.get("clock_mode") != "NATIVE":
        raise ValueError("NATIVE_SIGNAL_CLOCKS_REQUIRED")
    signal = payload.get("signal_at") or runtime.get("signal_at")
    decision = payload.get("decision_at") or runtime.get("decision_at")
    if not decision or (require_signal and not signal):
        raise ValueError("NATIVE_SIGNAL_CLOCKS_REQUIRED")
    if stamp(decision) > at or (signal and stamp(signal) > stamp(decision)):
        raise ValueError("NATIVE_SIGNAL_CLOCK_ORDER_INVALID")
    quote = payload.get("quote_used") or {}
    identity(quote)
    if not quote.get("observed_at") or stamp(quote["observed_at"]) > stamp(decision):
        raise ValueError("NATIVE_RECEIPT_CLOCK_INVALID")
    for clock in ("book_at", "trade_at"):
        if quote.get(clock) and stamp(quote[clock]) > stamp(quote["observed_at"]):
            raise ValueError("NATIVE_PROVIDER_RECEIPT_CLOCK_INVALID")
    if not runtime.get("strategy_id") or not runtime.get("configuration_fingerprint"):
        raise ValueError("NATIVE_STRATEGY_CONFIGURATION_REQUIRED")
    return payload


def preregister_entry_evaluators(*, registered_at, strategy_id, definitions=None,
                                horizons=(300, 900), max_age_seconds=120):
    registered = stamp(registered_at)
    if not str(strategy_id or "").strip():
        raise ValueError("EXPLICIT_ENTRY_STRATEGY_REQUIRED")
    defs = _json(DEFINITIONS if definitions is None else definitions)
    if not isinstance(defs, list) or not 1 <= len(defs) <= 16:
        raise ValueError("INVALID_ENTRY_DEFINITIONS")
    allowed = {d["kind"] for d in DEFINITIONS}
    seen = set()
    for d in defs:
        if set(d) - {"id", "version", "kind", "threshold", "minimum_rvol", "maximum_spread_bps"}:
            raise ValueError("UNKNOWN_ENTRY_HYPOTHESIS_PARAMETER")
        if not d.get("id") or not d.get("version") or d.get("kind") not in allowed or d["id"] in seen:
            raise ValueError("INVALID_ENTRY_DEFINITION")
        seen.add(d["id"])
        for field in ("threshold", "minimum_rvol", "maximum_spread_bps"):
            if field in d:
                number(d[field], nonnegative=field != "threshold")
    if (not isinstance(horizons, (list, tuple)) or not 1 <= len(horizons) <= 4 or
            any(isinstance(h, bool) or not isinstance(h, int) or not 1 <= h <= 7200 for h in horizons) or
            len(set(horizons)) != len(horizons) or isinstance(max_age_seconds, bool) or
            not isinstance(max_age_seconds, int) or not 1 <= max_age_seconds <= 3600):
        raise ValueError("INVALID_ENTRY_LABEL_HORIZON")
    payload = {"schema": REGISTRY_SCHEMA, "strategy_id": str(strategy_id), "registered_at": registered.isoformat(),
        "definitions": defs, "horizons": sorted(horizons), "max_age_seconds": max_age_seconds,
        "score_is_probability": False, "entry_authority": False, "promotion": "EXPLICIT_POLICY_ONLY",
        "design_basis": "PREDECLARED_HYPOTHESES; no retrospective fitting or winner selection"}
    return payload | {"registry_sha256": digest(payload)}


def _registry_valid(registry):
    payload = {k: v for k, v in registry.items() if k != "registry_sha256"}
    if registry.get("schema") != REGISTRY_SCHEMA or digest(payload) != registry.get("registry_sha256"):
        raise ValueError("ENTRY_REGISTRY_DIGEST_INVALID")


def _optional_feature(native, name, decision):
    value = native.get(name)
    if not isinstance(value, dict) or not value.get("source") or not value.get("source_at") or not value.get("available_at"):
        return None
    if not stamp(value["source_at"]) <= stamp(value["available_at"]) <= decision:
        raise ValueError("FUTURE_ENTRY_FEATURE")
    return value


def _signal_input_fingerprint(payload):
    """Distinct native market inputs, excluding receipt/decision clock churn.

    The immutable payload's raw SHA is retained separately. Re-receiving the
    same provider event does not create another market sample or opportunity.
    """
    q, runtime = payload["quote_used"], payload["runtime"]
    inputs = payload.get("inputs_used") or {}
    native = inputs.get("entry_signal_inputs") or {}
    features = {k: native.get(k, inputs.get(k)) for k in (
        "momentum", "samples", "spread", "spread_fraction", "signal_window_minutes", "eod_at", "price_sample_status")}
    points = native.get("price_samples")
    features["price_samples"] = [{k: p.get(k) for k in (
        "price", "source_at", "source", "symbol", "asset_class", "settlement", "currency", "market")}
        for p in points if isinstance(p, dict)] if isinstance(points, list) else None
    for name in ("rvol", "regime"):
        value = native.get(name)
        features[name] = {k: value.get(k) for k in ("value", "source", "source_at", "units", "basis")} if isinstance(value, dict) else None
    return digest([identity(q), runtime["strategy_id"], runtime.get("strategy_version"),
        runtime["configuration_fingerprint"], {k: q.get(k) for k in (
            "book_at", "trade_at", "last", "last_kind", "bid", "ask", "bid_size", "ask_size", "source", "metadata_source")}, features])


def _project(payload, registry):
    """Strict input projection: future labels and final gates cannot enter it."""
    decision = stamp(payload.get("decision_at") or payload["runtime"]["decision_at"])
    signal = stamp(payload.get("signal_at") or payload["runtime"]["signal_at"])
    if not stamp(registry["registered_at"]) < signal <= decision:
        raise ValueError("ENTRY_NOT_AFTER_PREREGISTRATION")
    quote = dict(payload["quote_used"])
    quote["source"] = quote.get("metadata_source") or quote.get("source")
    error = usable_book(quote, decision, max_age_seconds=registry["max_age_seconds"])
    if error:
        raise ValueError(error.upper())
    if quote["asset_class"] == "FUTUROS":
        from bs_instrument_contracts import InstrumentContract
        from rc6_paper_family_lifecycle import _validate_future_contract
        try:
            contract = InstrumentContract(**quote["financial_contract"])
            _validate_future_contract(contract)
            if (contract.symbol, contract.family, contract.settlement, contract.currency, contract.market) != identity(quote):
                raise ValueError("EXACT_FUTURES_CONTRACT_IDENTITY_MISMATCH")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("EXACT_FUTURES_CONTRACT_REQUIRED") from exc
    inputs = payload.get("inputs_used") or {}
    native = inputs.get("entry_signal_inputs") or {}
    if native and native.get("schema") != NATIVE_INPUT_SCHEMA:
        raise ValueError("NATIVE_ENTRY_INPUT_SCHEMA_INVALID")
    action = payload["decision"].get("action")
    if action is None and payload["decision"].get("technical_gate") == "APPROVE":
        action = "BUY"
    score = payload["decision"].get("score")
    if score is None:
        score = (inputs.get("candidate") or {}).get("score")
    features = {"momentum": native.get("momentum", inputs.get("momentum")),
                "samples": native.get("samples", inputs.get("samples")),
                "spread": native.get("spread", inputs.get("spread")), "volatility": None,
                "rvol": None, "eod_at": None, "regime": "NO_VERIFICADO"}
    points = native.get("price_samples")
    if native.get("price_sample_status") == "NO_VERIFICADO":
        points = None
    if points is not None:
        if not isinstance(points, list) or len(points) > 500:
            raise ValueError("NATIVE_PRICE_VECTOR_BUDGET")
        values, seen, previous = [], set(), None
        for p in points:
            if not isinstance(p, dict) or not p.get("source") or not p.get("source_at") or not p.get("received_at"):
                raise ValueError("NATIVE_PRICE_VECTOR_PROVENANCE_REQUIRED")
            source, received = stamp(p["source_at"]), stamp(p["received_at"])
            if not source <= received <= signal or (previous is not None and source <= previous):
                raise ValueError("NATIVE_PRICE_VECTOR_CLOCK_INVALID")
            if any(p.get(field) and str(p[field]).upper() != str(quote[field]).upper()
                   for field in ("symbol", "asset_class", "settlement", "currency", "market")):
                raise ValueError("NATIVE_PRICE_VECTOR_IDENTITY_MISMATCH")
            if source in seen:
                raise ValueError("NATIVE_PRICE_VECTOR_DUPLICATE")
            seen.add(source); previous = source
            values.append(number(p["price"], positive=True))
        if features["samples"] is not None and int(features["samples"]) != len(values):
            raise ValueError("NATIVE_PRICE_VECTOR_SAMPLE_MISMATCH")
        if len(values) >= 3:
            returns = [b / a - 1 for a, b in zip(values, values[1:])]
            sigma = Decimal(str(stdev(returns)))
            if sigma > 0:
                features["volatility"] = str(sigma)
    rvol = _optional_feature(native, "rvol", signal)
    if rvol and rvol.get("units") == "RATIO" and rvol.get("basis") == "SAME_MINUTE_HISTORICAL_PROFILE":
        features["rvol"] = str(number(rvol["value"], nonnegative=True))
    regime = _optional_feature(native, "regime", signal)
    if regime:
        features["regime"] = str(regime.get("value") or "NO_VERIFICADO")
    if native.get("eod_at"):
        eod = stamp(native["eod_at"])
        features["eod_at"] = eod.isoformat()
    return _json({"schema": "rc6.entry-experiment-input.v1", "identity": identity(quote), "quote": quote,
        "strategy_id": payload["runtime"]["strategy_id"], "strategy_version": payload["runtime"].get("strategy_version"),
        "signal_at": signal.isoformat(), "decision_at": decision.isoformat(), "features": features,
        "baseline_signal": {"action": action, "score": score, "score_is_probability": False},
        "native_price_samples": points, "native_input_sha256": digest(native),
        "native_market_input_sha256": _signal_input_fingerprint(payload),
        "native_configuration_fingerprint": payload["runtime"]["configuration_fingerprint"],
        "native_git_sha": payload["runtime"].get("git_sha"),
        "native_source_status": payload["runtime"].get("source_status", "NO_VERIFICADO"),
        "registry_sha256": registry["registry_sha256"], "pre_registered_at": registry["registered_at"]})


def _evaluate(definition, snapshot, *, decision_at):
    f, q = snapshot["features"], snapshot["quote"]
    result = {"status": "NO_VERIFICADO", "accepted": None, "score": None,
              "score_is_probability": False, "entry_authority": False, "reason": "CAUSAL_FEATURE_UNAVAILABLE"}
    kind = definition["kind"]
    if kind == "native":
        base = snapshot["baseline_signal"]
        if base.get("score") is None or not base.get("action"):
            return result | {"reason": "NATIVE_BASELINE_SIGNAL_UNAVAILABLE"}
        return result | {"status": "EVALUATED", "score": str(number(base["score"])),
            "accepted": base["action"] in {"BUY", "BUY_CANDIDATE"}, "action": base["action"], "reason": "UNCHANGED_NATIVE_BASELINE"}
    if kind == "microstructure":
        if q.get("bid_size") is None or q.get("ask_size") is None:
            return result | {"reason": "NATIVE_DEPTH_UNAVAILABLE"}
        bid, ask = number(q["bid_size"], nonnegative=True), number(q["ask_size"], nonnegative=True)
        if bid + ask <= 0:
            return result | {"reason": "INSUFFICIENT_DEPTH"}
        score = (bid - ask) / (bid + ask)
        spread = (number(q["ask"]) / number(q["bid"]) - 1) * 10000
        accepted = score > number(definition.get("threshold", 0)) and spread <= number(definition.get("maximum_spread_bps", 50))
    else:
        if f.get("momentum") is None:
            return result | {"reason": "NATIVE_MOMENTUM_UNAVAILABLE"}
        momentum = number(f["momentum"])
        if kind == "momentum_volatility":
            if f.get("volatility") is None:
                return result | {"reason": "CAUSAL_VOLATILITY_UNAVAILABLE"}
            score = momentum / number(f["volatility"], positive=True)
            accepted = score > number(definition.get("threshold", 0))
        elif kind == "momentum_activity":
            if f.get("rvol") is None:
                return result | {"reason": "CAUSAL_RVOL_UNAVAILABLE"}
            score = momentum * number(f["rvol"], nonnegative=True)
            accepted = score > number(definition.get("threshold", 0)) and number(f["rvol"]) >= number(definition.get("minimum_rvol", 1))
        elif kind == "time_horizon":
            if not f.get("eod_at"):
                return result | {"reason": "NATIVE_EOD_HORIZON_UNAVAILABLE"}
            remaining = (stamp(f["eod_at"]) - stamp(decision_at)).total_seconds()
            horizon = snapshot["label_horizon_seconds"]
            score = momentum * Decimal(str(max(0, min(1, remaining / horizon))))
            accepted = remaining >= horizon and score > number(definition.get("threshold", 0))
        else:
            raise ValueError("UNREGISTERED_ENTRY_EVALUATOR")
    return result | {"status": "EVALUATED", "score": str(score), "accepted": bool(accepted),
        "reason": "PREREGISTERED_HYPOTHESIS_CANDIDATE" if accepted else "PREREGISTERED_HYPOTHESIS_REJECTED"}


def evaluate_entry_snapshot(snapshot, registry, *, decision_at):
    _registry_valid(registry)
    if (str(snapshot.get("strategy_id")).upper() != str(registry["strategy_id"]).upper() or
            not stamp(registry["registered_at"]) < stamp(snapshot["signal_at"]) <= stamp(decision_at)):
        raise ValueError("ENTRY_STRATEGY_OR_FREEZE_MISMATCH")
    if stamp(snapshot["decision_at"]) != stamp(decision_at):
        raise ValueError("ENTRY_DECISION_CLOCK_MISMATCH")
    for field in ("label", "labels", "forward_label", "future_books", "net_pnl", "exit_reason"):
        if field in snapshot:
            raise ValueError("OUTCOME_NOT_ENTRY_INPUT")
    evaluators = {}
    for definition in registry["definitions"]:
        evaluators[definition["id"] + ":" + definition["version"]] = (
            lambda data, *, decision_at, d=definition: _evaluate(d, data, decision_at=decision_at))
    return evaluate_same_snapshot(snapshot, evaluators, decision_at=stamp(decision_at).isoformat())


def _book(row):
    q = {k: row.get(k) for k in READ_COLUMNS["market_snapshots"] if k not in {"metadata_source", "source"}}
    q["source"] = row.get("metadata_source") or row.get("source")
    return q


def _label_cost(payload, label, book, entry_price):
    q, native = payload["quote_used"], (payload.get("inputs_used") or {}).get("entry_signal_inputs") or {}
    try:
        family = q["asset_class"]
        if family in {"ACCIONES", "CEDEARS", "ETFS"}:
            fees, factor = paper_fee_model(family), Decimal(1)
        else:
            factor = number((q.get("financial_contract") or {}).get("cash_multiplier"), positive=True)
            if native.get("cash_multiplier") is not None and number(native["cash_multiplier"], positive=True) != factor:
                raise ValueError("FROZEN_CONTRACT_MULTIPLIER_MISMATCH")
            if native.get("fee_model"):
                fees = FeeModel(**native["fee_model"])
            else:
                if not native.get("fee_provenance"):
                    raise ValueError("FROZEN_FEE_PROVENANCE_REQUIRED")
                fees = FeeModel(number(native.get("fee_rate"), nonnegative=True), 0, 0, False, False,
                                str(native["fee_provenance"]) + "; FROZEN_PAPER_ESTIMATE; broker terms NO_VERIFICADO")
        qty = number(native.get("quantity", 1), positive=True)
        exit_price = number(book["bid"], positive=True)
        intraday = stamp(label["decision_at"]).astimezone(ART).date() == stamp(book["book_at"]).astimezone(ART).date() and q["market"] == "BYMA" and family != "FUTUROS"
        costs = expected_round_trip_cost(entry_price, exit_price, qty, factor, fees, intraday_eligible=intraday)
        gross = (exit_price - entry_price) * qty * factor
        return _json({"status": "MODELED_PAPER_SENSITIVITY", "currency": q["currency"], "gross": gross,
            "costs": costs, "net": gross - costs["total_expected_friction"],
            "entry_notional": entry_price * qty * factor,
            "net_return": (gross - costs["total_expected_friction"]) / (entry_price * qty * factor),
            "sizing_basis": "NATIVE_SIZE" if native.get("quantity") else "UNIT_DIAGNOSTIC",
            "execution_guaranteed": False, "economic_edge_validated": False})
    except (ValueError, KeyError, TypeError):
        return {"status": "NO_VERIFICADO", "reason": "FROZEN_COST_OR_CONTRACT_INPUT_UNAVAILABLE", "currency": q["currency"]}


def _cohorts(records):
    groups = defaultdict(list)
    for record in records:
        for label in record.get("labels", []):
            if label.get("status") != "MEDIDO":
                continue
            inp = record["input"]
            for variant, output in record["variants"].items():
                decision = output["decision"]
                if decision.get("score") is None or decision.get("status") != "EVALUATED":
                    continue
                ident = inp["identity"]
                key = (inp["strategy_id"], variant, label["horizon_seconds"], ident[1], tuple(ident),
                    stamp(inp["decision_at"]).astimezone(ART).hour, inp["features"]["regime"], ident[3],
                    inp.get("strategy_version") or "NO_VERIFICADO", inp["native_configuration_fingerprint"],
                    inp["registry_sha256"])
                groups[key].append((label, decision))
    reports = []
    for key, values in sorted(groups.items()):
        labels = [v[0] for v in values]
        scores = [number(v[1]["score"]) for v in values]
        returns = [number(l["gross_forward_return"]) for l in labels]
        candidates = [(l, d) for l, d in values if d.get("accepted") is True]
        net_values = [(l, d) for l, d in values if
            l.get("economic_sensitivity", {}).get("status") == "MODELED_PAPER_SENSITIVITY"]
        net_returns = [number(l["economic_sensitivity"]["net_return"]) for l, _ in net_values]
        net_candidates = [(l, d) for l, d in net_values if d.get("accepted") is True]
        reports.append(_json({"strategy_id": key[0], "variant": key[1], "horizon_seconds": key[2],
            "family": key[3], "identity": key[4], "entry_hour_art": key[5], "regime": key[6], "currency": key[7],
            "native_strategy_version": key[8], "native_configuration_fingerprint": key[9], "registry_sha256": key[10],
            "observations": len(values), "candidate_labels": len(candidates),
            "distinct_native_market_inputs": len({l["native_market_input_sha256"] for l in labels}),
            "denominator_basis": "NATIVE_DECISION_EVALUATIONS; repeated market inputs are not independent opportunities",
            "auc_gross": auc(scores, [r > 0 for r in returns]),
            "hit_rate_gross": sum(r > 0 for r in returns) / len(returns),
            "candidate_hit_rate_gross": sum(number(l["gross_forward_return"]) > 0 for l, _ in candidates) / len(candidates) if candidates else None,
            "net_economic_observations": len(net_values),
            "auc_modeled_net": auc([number(d["score"]) for _, d in net_values], [r > 0 for r in net_returns]),
            "hit_rate_modeled_net": sum(r > 0 for r in net_returns) / len(net_returns) if net_returns else None,
            "candidate_hit_rate_modeled_net": sum(number(l["economic_sensitivity"]["net_return"]) > 0 for l, _ in net_candidates) / len(net_candidates) if net_candidates else None,
            "modeled_net_forward_returns": distribution(net_returns),
            "forward_returns": distribution(returns), "mfe_observed": distribution(l["mfe_observed"] for l in labels),
            "mae_observed": distribution(l["mae_observed"] for l in labels), "score_is_probability": False,
            "path_censored_observations": sum(bool(l.get("path_censored")) for l in labels),
            "continuous_mfe_mae_coverage": "NO_VERIFICADO", "observed_excursions_only": True,
            "net_cost_status": Counter(l.get("economic_sensitivity", {}).get("status") for l in labels),
            "economic_edge_validated": False, "sample_is_independent": False}))
    return reports


def evaluate_runtime_entry_signals(database, *, as_of, previous=None, row_limit=200, registry_config=None):
    """Return (report, checkpoint) for the canonical worker's existing writer."""
    at = stamp(as_of)
    config = _json(registry_config or {})
    if set(config) - {"strategies", "definitions", "horizons", "max_age_seconds"}:
        raise ValueError("UNKNOWN_ENTRY_REGISTRY_CONFIG")
    strategies = config.get("strategies", list(STRATEGIES))
    if not isinstance(strategies, list) or not 1 <= len(strategies) <= 20 or len(set(str(s).upper() for s in strategies)) != len(strategies):
        raise ValueError("INVALID_ENTRY_STRATEGY_REGISTRY")
    fingerprint = digest({"schema": SCHEMA, "config": config, "definitions": DEFINITIONS,
        "bounds": [row_limit, MAX_ACTIVE, MAX_ARCHIVED, MAX_BOOKS], "provider_requests": 0,
        "cohort_contract": "EXACT_IDENTITY_NATIVE_VERSION_CONFIGURATION_AND_FROZEN_REGISTRY",
        "distinct_input_contract": "PROVIDER_CLOCK_VALUES; receipt and decision clock churn excluded"})
    invalidation = None
    if previous:
        if len(canonical(previous).encode()) > MAX_CHECKPOINT_BYTES or previous.get("checkpoint_sha256") != _fingerprint(previous) or previous.get("schema") != SCHEMA:
            invalidation = "CHECKPOINT_DIGEST_SCHEMA_OR_BUDGET_INVALID"
        elif previous.get("configuration_fingerprint") != fingerprint:
            invalidation = "ENTRY_CONFIGURATION_CHANGED"
        elif stamp(previous["last_as_of"]) > at:
            raise ValueError("ENTRY_CHECKPOINT_FROM_FUTURE")
    prior = None if invalidation else previous
    source = _read_rows(database, as_of=at, tables=("decision_evidence_snapshots", "market_snapshots"),
        cursors=(prior or {}).get("cursors"), source_key=(prior or {}).get("source_key"), row_limit=row_limit)
    rejections = Counter()
    if source["bootstrap"]:
        if prior:
            invalidation = "SOURCE_CHANGED_OR_CURSOR_REVERSED"
        registries = {str(strategy).upper(): preregister_entry_evaluators(registered_at=at, strategy_id=strategy,
            definitions=config.get("definitions"), horizons=config.get("horizons", (300, 900)),
            max_age_seconds=config.get("max_age_seconds", 120)) for strategy in strategies}
        checkpoint = {"schema": SCHEMA, "source_key": source["source_key"], "started_at": at.isoformat(),
            "last_as_of": at.isoformat(), "configuration_fingerprint": fingerprint, "cursors": source["tails"],
            "registries": registries, "active": {}, "archive": [], "registered": 0, "retired": 0,
            "censored": 0, "rejections": {}, "native_decisions_observed": 0}
        status = "START_AT_CURRENT_TAIL"
    else:
        checkpoint = _json(prior)
        status = "PROSPECTIVE_ENTRY_SHADOW_EVALUATED"
        for row in source["rows"]["decision_evidence_snapshots"]:
            # Integrity errors abort the entire tick so the caller cannot
            # persist a cursor beyond a corrupt immutable source snapshot.
            if row.get("_oversize"):
                rejections["IMMUTABLE_DECISION_ROW_OVERSIZE"] += 1
                checkpoint["cursors"]["decision_evidence_snapshots"] = row["_rowid"]
                continue
            try:
                payload = _native_payload(row, at)
                checkpoint["native_decisions_observed"] += 1
                strategy = str(payload["runtime"]["strategy_id"]).upper()
                registry = checkpoint["registries"].get(strategy)
                if registry is None or payload["quote_used"]["asset_class"] not in FAMILIES:
                    raise ValueError("STRATEGY_NOT_VALIDATED")
                projected = _project(payload, registry)
                key = row["decision_key"]
                if key not in checkpoint["active"] and not any(r["decision_key"] == key for r in checkpoint["archive"]):
                    if len(checkpoint["active"]) >= MAX_ACTIVE:
                        checkpoint["censored"] += 1
                        raise ValueError("ENTRY_EXPERIMENT_CAPACITY_CENSORED")
                    projected["label_horizon_seconds"] = max(registry["horizons"])
                    variants = evaluate_entry_snapshot(projected, registry, decision_at=projected["decision_at"])
                    checkpoint["active"][key] = {"decision_key": key, "input": projected, "variants": variants,
                        "payload": {"quote_used": payload["quote_used"], "inputs_used": {"entry_signal_inputs":
                            (payload.get("inputs_used") or {}).get("entry_signal_inputs") or {}}},
                        "registered_at": at.isoformat(), "horizons": registry["horizons"], "books": [], "labels": [],
                        "source_payload_sha256": row["payload_sha256"], "path_censored": bool(source["truncated"])}
                    checkpoint["registered"] += 1
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "NATIVE_ENTRY_INPUT_INVALID"
                if reason in {"IMMUTABLE_DECISION_HASH_MISMATCH", "IMMUTABLE_DECISION_KEY_MISMATCH"}:
                    raise
                rejections[reason] += 1
            checkpoint["cursors"]["decision_evidence_snapshots"] = row["_rowid"]
        books = []
        for row in source["rows"]["market_snapshots"]:
            checkpoint["cursors"]["market_snapshots"] = row["_rowid"]
            if row.get("_oversize"):
                rejections["FUTURE_BOOK_ROW_OVERSIZE"] += 1
                continue
            try:
                book = _book(row)
                if usable_book(book, book["observed_at"]) or stamp(book["observed_at"]) > at:
                    raise ValueError("FUTURE_LABEL_BOOK_INVALID")
                identity(book)
                books.append(book)
            except (ValueError, TypeError, KeyError):
                rejections["FUTURE_LABEL_BOOK_INVALID"] += 1
        for key, record in list(checkpoint["active"].items()):
            record["path_censored"] |= bool(source["truncated"])
            inp, seen = record["input"], {b["book_at"] for b in record["books"]}
            for book in books:
                if (tuple(inp["identity"]) == identity(book) and stamp(inp["decision_at"]) < stamp(book["book_at"])
                        and stamp(book["observed_at"]) > stamp(record["registered_at"]) and book["book_at"] not in seen):
                    record["books"].append(book); seen.add(book["book_at"])
            record["books"].sort(key=lambda b: (stamp(b["observed_at"]), stamp(b["book_at"])))
            if len(record["books"]) > MAX_BOOKS:
                record["path_censored"] = True
                record["books"] = record["books"][-MAX_BOOKS:]
            entry_price = number(inp["quote"]["ask"], positive=True)
            entry = {**inp["quote"], "decision_at": inp["decision_at"], "entry_price": str(entry_price)}
            labels = forward_labels(entry, record["books"], record["horizons"], as_of=at)
            for label in labels:
                label["price_anchor"] = "SHADOW_QUOTE_ASK_TO_FUTURE_BID"
                label["out_of_sample"] = True
                label["registry_sha256"] = inp["registry_sha256"]
                label["native_market_input_sha256"] = inp["native_market_input_sha256"]
                label["sample_is_independent"] = False
                label["path_censored"] = record["path_censored"]
                if label.get("status") == "MEDIDO":
                    target = stamp(inp["decision_at"]) + timedelta(seconds=label["horizon_seconds"])
                    book = next(b for b in record["books"] if target <= stamp(b["observed_at"]) <= target + timedelta(seconds=120))
                    label["future_book_at"] = book["book_at"]
                    label["future_received_at"] = book["observed_at"]
                    label["horizon_clock_basis"] = "NATIVE_RECEIPT_AVAILABILITY; provider book clock retained separately"
                    label["economic_sensitivity"] = _label_cost(record["payload"], label, book, entry_price)
            record["labels"] = _json(labels)
            if at > stamp(inp["decision_at"]) + timedelta(seconds=max(record["horizons"]) + 120):
                del record["books"]; del record["payload"]
                checkpoint["archive"].append(record)
                del checkpoint["active"][key]
                checkpoint["retired"] += 1
        checkpoint["archive"] = checkpoint["archive"][-MAX_ARCHIVED:]
        checkpoint["last_as_of"] = at.isoformat()
    for reason, count in rejections.items():
        checkpoint["rejections"][reason] = checkpoint["rejections"].get(reason, 0) + count
    checkpoint["checkpoint_sha256"] = _fingerprint(checkpoint)
    if len(canonical(checkpoint).encode()) > MAX_CHECKPOINT_BYTES:
        raise ValueError("ENTRY_CHECKPOINT_CAPACITY_EXCEEDED")
    records = checkpoint["archive"] + list(checkpoint["active"].values())
    report = {"schema": SCHEMA, "as_of": at.isoformat(), "status": status, "mode": "SHADOW",
        "entry_authority": False, "decision_effect": "NONE", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
        "real_order_routes": [], "provider_requests": 0, "source_database_effect": "READ_ONLY", "legacy_history_backfill": False,
        "configuration_fingerprint": fingerprint, "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "checkpoint_invalidation": invalidation, "registries": checkpoint["registries"], "registered": checkpoint["registered"],
        "retired": checkpoint["retired"], "censored": checkpoint["censored"], "rejections": checkpoint["rejections"],
        "source_read_truncated": source["truncated"], "native_decisions_observed": checkpoint["native_decisions_observed"],
        "experiments": [{k: v for k, v in r.items() if k not in {"books", "payload"}} for r in records],
        "cohorts": _cohorts(records), "cohort_scope": {"basis": "bounded strictly postfreeze prospective inputs",
            "maximum_active": MAX_ACTIVE, "maximum_archive": MAX_ARCHIVED,
            "archive_truncated": checkpoint["retired"] > len(checkpoint["archive"])},
        "sample_is_independent": False, "score_is_probability": False, "score_calibration_oos": "NO_VERIFICADO",
        "economic_edge_validated": False, "future_profitability": "NO_VERIFICADO_EXTERNAL_RUNTIME_ONLY"}
    return _json(report), checkpoint
