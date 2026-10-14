"""Prospective, bounded runtime bridge to the existing causal SHADOW laboratory.

The source is opened read-only, never through PaperStore.  The caller durably
stores the returned JSON checkpoint outside the trading database.  A new
checkpoint starts at current tails: old positions and quotes are not replayed.
Only entries observed after that watermark can be registered; only observations
received after registration can change their exit comparison.  No broker,
provider, factual parameter, score, or order route has authority in this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median, stdev
from time import monotonic

from rc6_dynamic_universe.economics import preregister_exit_variants, shadow_economics
from rc6_performance.common import canonical, digest, identity, number, stamp
from rc6_performance.costs import FeeModel, ledger_leg_cost, paper_fee_model
from rc6_performance.replay import ExitPolicy, ExitReplay
from rc6_performance.shadow import fit_movement, forward_labels, usable_book

SCHEMA = "rc6.runtime-shadow-lab.v1"
QUERY_SECONDS = .25
MAX_ACTIVE = 16
MAX_ARCHIVED = 64
MAX_IDENTITIES = 100
HISTORY_POINTS = 32
MAX_ROW_BYTES = 65536
MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
MODEL_HORIZON_SECONDS = 900
MIN_MODEL_LABELS = 30
HYPOTHESES = {"target_sigma": 2, "stop_sigma": 1, "max_hold_seconds": 1800,
              "trailing_fraction": "0.008", "minimum_net_lock": "0.002"}
TABLES = ("paper_positions", "market_snapshots", "decision_evidence_snapshots")
READ_COLUMNS = {
    "paper_positions": ("paper_id", "source", "symbol", "asset_class", "settlement", "currency", "market",
        "status", "quantity", "entry_price", "entry_cost", "stop_price", "target_price", "opened_at", "features_json"),
    "market_snapshots": ("source", "metadata_source", "symbol", "asset_class", "settlement", "currency", "market",
        "observed_at", "book_at", "trade_at", "last_kind", "last", "bid", "ask", "bid_size", "ask_size"),
    "decision_evidence_snapshots": ("decision_key", "captured_at", "payload_sha256", "payload_json"),
}
REPLAY_FIELDS = ("remaining", "high", "last_at", "reason", "detected_at", "fills",
                 "used_depth", "last_rejection", "break_even_armed")


def _json(value):
    return json.loads(canonical(value))


def _settings(runtime_config):
    # These are the actual canonical runtime constructor defaults. They are
    # frozen prospectively, never attributed to a historical factual entry.
    from bq_exit_policy import PaperSessionPolicy, SESSION_SOURCE
    policy = PaperSessionPolicy()
    settings = {"session_policy": _json(asdict(policy)), "session_source": SESSION_SOURCE,
                "max_age_seconds": int(os.getenv("PAPER_BOOK_MAX_AGE_SECONDS", "120")),
                "max_hold_minutes": int(os.getenv("PAPER_MAX_HOLD_MINUTES", "360")),
                "slippage": "0.0002", "participation": "0.10",
                "eod_policy": os.getenv("PAPER_EOD_POLICY", "FORCE_CLOSE").upper(),
                "intraday_fee_rebate": os.getenv("PAPER_INTRADAY_FEE_REBATE", "true").lower()
                   in {"1", "true", "yes", "si", "sí"},
                "source": "bv_paper_runtime.broker_from_environment+PaperBroker.defaults"}
    if runtime_config is not None:
        if not isinstance(runtime_config, dict) or set(runtime_config) - set(settings):
            raise ValueError("UNKNOWN_SHADOW_RUNTIME_SETTINGS")
        settings.update(_json(runtime_config))
    if (isinstance(settings["max_age_seconds"], bool) or
            not 1 <= int(settings["max_age_seconds"]) <= 3600 or
            isinstance(settings["max_hold_minutes"], bool) or
            not 1 <= int(settings["max_hold_minutes"]) <= 1440 or
            not isinstance(settings["intraday_fee_rebate"], bool)):
        raise ValueError("INVALID_SHADOW_RUNTIME_SETTINGS")
    if not 0 < number(settings["participation"]) <= 1 or not 0 <= number(settings["slippage"]) < 1:
        raise ValueError("INVALID_SHADOW_EXECUTION_MODEL")
    values = settings["session_policy"]
    policy = PaperSessionPolicy(**{**values, "open_time": time.fromisoformat(values["open_time"]),
                                  "close_time": time.fromisoformat(values["close_time"])})
    settings["session_policy"] = _json(asdict(policy))
    return settings, policy


def _checkpoint_hash(checkpoint):
    return digest({key: value for key, value in checkpoint.items() if key != "checkpoint_sha256"})


def _book(row):
    # An absent native clock is never replaced by observation/capture time.
    keys = ("symbol", "asset_class", "settlement", "currency", "market", "bid", "ask",
            "bid_size", "ask_size", "observed_at", "book_at", "trade_at", "last", "last_kind")
    book = {key: row.get(key) for key in keys}
    book["source"] = row.get("metadata_source") or row.get("source")
    return book


def _read(database, at, previous, row_limit):
    path = Path(database).resolve(strict=True)
    stat = path.stat()
    source_key = digest([str(path), stat.st_dev, stat.st_ino])
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.005)
    connection.row_factory = sqlite3.Row
    deadline = monotonic() + QUERY_SECONDS
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=5")
        connection.set_progress_handler(lambda: int(monotonic() > deadline), 200)
        if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise ValueError("SHADOW_SOURCE_WAL_REQUIRED")
        connection.execute("BEGIN")
        state = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not state or tuple(state) != ("PRODUCTION_PAPER", 0):
            raise ValueError("SHADOW_PAPER_SAFETY_REQUIRED")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1000")}
        if not set(TABLES) <= tables:
            raise ValueError("SHADOW_LAB_SOURCE_TABLES_UNAVAILABLE")
        tails = {table: connection.execute(f'SELECT COALESCE(MAX(rowid),0) FROM "{table}"').fetchone()[0]
                 for table in TABLES}
        valid_previous = previous if previous and previous.get("source_key") == source_key else None
        if valid_previous and any(tails[key] < valid_previous["cursors"][key] for key in TABLES):
            valid_previous = None
        if valid_previous is None:
            existing = list(connection.execute("SELECT paper_id FROM paper_positions WHERE status='OPEN' LIMIT ?",
                                               (row_limit + 1,)))
            return source_key, tails, {}, len(existing[:row_limit]), len(existing) > row_limit
        rows, truncated = {}, False
        for table in TABLES:
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            selected = ["rowid AS _rowid"]
            for column in READ_COLUMNS[table]:
                if column not in columns:
                    selected.append(f'NULL AS "{column}"')
                else:
                    # Do not materialize an unbounded source JSON/blob into
                    # the worker. Oversize rows retain their cursor/rejection.
                    selected.append(f'CASE WHEN length(CAST("{column}" AS BLOB))>{MAX_ROW_BYTES} '
                                    f'THEN NULL ELSE "{column}" END AS "{column}"')
            size_terms = [f'COALESCE(length(CAST("{column}" AS BLOB)),0)' for column in READ_COLUMNS[table]
                          if column in columns]
            selected.append(f'({"+".join(size_terms)}>{MAX_ROW_BYTES}) AS _oversize')
            batch = list(connection.execute(f'SELECT {",".join(selected)} FROM "{table}" WHERE rowid>? '
                                            'ORDER BY rowid LIMIT ?', (valid_previous["cursors"][table], row_limit + 1)))
            rows[table] = [dict(row) for row in batch[:row_limit]]
            truncated |= len(batch) > row_limit
        return source_key, tails, rows, 0, truncated
    finally:
        connection.rollback()
        connection.close()


def _native_evidence(row, at):
    text = row["payload_json"]
    if not isinstance(text, str) or len(text.encode()) > MAX_ROW_BYTES:
        raise ValueError("IMMUTABLE_DECISION_OVERSIZE")
    if hashlib.sha256(text.encode()).hexdigest() != row["payload_sha256"]:
        raise ValueError("IMMUTABLE_DECISION_HASH_MISMATCH")
    value = json.loads(text)
    decision, runtime = value.get("decision") or {}, value.get("runtime") or {}
    paper_id = decision.get("paper_id")
    if not paper_id or decision.get("final_result") != "OPENED_SIMULATED":
        return None
    clocks = {key: value.get(key) or runtime.get(key) for key in
              ("signal_at", "decision_at", "intent_at", "entry_fill_committed_at")}
    if runtime.get("clock_mode") != "NATIVE" or any(not clocks[key] for key in clocks):
        raise ValueError("ENTRY_NATIVE_CLOCKS_UNAVAILABLE")
    parsed = [stamp(clocks[key]) for key in clocks]
    if parsed != sorted(parsed) or parsed[-1] > at or stamp(value["captured_at"]) > parsed[0]:
        raise ValueError("ENTRY_FUTURE_OR_REVERSED_CLOCKS")
    quote = _book(value.get("quote_used") or {})
    error = usable_book(quote, parsed[1])
    if error:
        raise ValueError("ENTRY_BOOK_" + error.upper())
    return {"paper_id": str(paper_id), "clocks": clocks, "quote": quote,
            "score": decision.get("score"), "strategy_id": runtime.get("strategy_id"),
            "decision_key": value.get("decision_key"), "payload_sha256": row["payload_sha256"],
            "configuration_fingerprint": runtime.get("configuration_fingerprint"),
            "inputs_used": {key: value.get("inputs_used", {}).get(key) for key in
                            ("exit_policy", "execution_style", "scalping_max_hold_minutes")}}


def _volatility(history, entry, decision_at):
    points = history.get(digest(identity(entry)), [])
    decision = stamp(decision_at)
    eligible, seen = [], set()
    for point in points:
        book = point["book"]
        try:
            sourced = stamp(book["trade_at"])
            if not sourced <= stamp(book["observed_at"]) <= stamp(point["available_at"]) < decision:
                continue
            if book["last_kind"] != "TRADE" or sourced in seen or (decision-sourced).total_seconds() > 5400:
                continue
            eligible.append((sourced, number(book["last"], positive=True), point))
            seen.add(sourced)
        except (ValueError, TypeError, KeyError):
            continue
    eligible.sort(key=lambda item: item[0])
    if len(eligible) < 6:
        return None, "PRE_ENTRY_DISTINCT_VOLATILITY_UNAVAILABLE"
    intervals = [(b[0]-a[0]).total_seconds() for a, b in zip(eligible, eligible[1:])]
    horizon = median(intervals)
    if horizon < 1 or max(intervals) > 2 * horizon:
        return None, "PRE_ENTRY_VOLATILITY_HORIZON_UNVERIFIED"
    returns = [float(b[1]/a[1]-1) for a, b in zip(eligible, eligible[1:])]
    value = stdev(returns)
    if value < .0000001:
        return None, "PRE_ENTRY_VOLATILITY_RANGE_UNVERIFIED"
    return {"value": value, "horizon_seconds": horizon,
            "available_at": max(stamp(item[2]["available_at"]) for item in eligible).isoformat(),
            "observations": len(eligible), "input_sha256": digest([item[2] for item in eligible]),
            "method": "preregistered sample stdev of distinct native-trade returns; no directional forecast"}, None


def _restore(entry, policy, fees, kwargs, state=None):
    laboratory = ExitReplay(entry, ExitPolicy(**policy), FeeModel(**fees), **kwargs)
    if state:
        for key in REPLAY_FIELDS:
            value = state[key]
            if key in {"remaining", "high"}:
                value = number(value)
            elif key == "last_at":
                value = stamp(value)
            elif key == "used_depth":
                value = {clock: number(quantity) for clock, quantity in value.items()}
            elif key == "fills":
                value = [{key: number(value) if key in {"quantity", "price", "gross", "costs", "net"} else value
                          for key, value in fill.items()} for fill in value]
            setattr(laboratory, key, value)
    return laboratory


def _freeze_replay(laboratory):
    result = {key: getattr(laboratory, key) for key in REPLAY_FIELDS}
    # Native book timestamps are strictly increasing before replay. Only the
    # last budget can be revisited; discarded old depth cannot be re-executed.
    used = result["used_depth"]
    result["used_depth"] = {max(used, key=stamp): used[max(used, key=stamp)]} if used else {}
    return _json(result)


def _model(checkpoint, entry, decision_at):
    labels = [record.get("movement_label", {}) for record in checkpoint["archive"]]
    cutoff = min(stamp(checkpoint["last_as_of"]), stamp(decision_at)-timedelta(microseconds=1))
    labels = [label for label in labels if label.get("status") != "MEDIDO" or stamp(label["label_available_at"]) <= cutoff]
    try:
        return fit_movement(labels, training_cutoff=cutoff.isoformat(),
                            horizon_seconds=MODEL_HORIZON_SECONDS, family=entry["asset_class"],
                            currency=entry["currency"], minimum_observations=MIN_MODEL_LABELS)
    except ValueError:
        return None


def _register(row, evidence, checkpoint, settings, session_policy, at):
    features_text = row.get("features_json") or "{}"
    if len(features_text.encode()) > MAX_ROW_BYTES:
        raise ValueError("ENTRY_FEATURES_OVERSIZE")
    features = json.loads(features_text)
    ident = identity(row)
    if ident[1] not in {"ACCIONES", "CEDEARS", "ETFS"}:
        raise ValueError("SPECIALIZED_LIFECYCLE_OUTSIDE_EXIT_LAB")
    if row.get("source") != "PRODUCTION_PAPER" or not str(row["paper_id"]).startswith("PAPER-"):
        raise ValueError("FACTUAL_PAPER_ENTRY_REQUIRED")
    opened = stamp(row["opened_at"])
    if not stamp(checkpoint["started_at"]) < opened <= at:
        raise ValueError("ENTRY_BEFORE_WATERMARK_OR_IN_FUTURE")
    if evidence is None:
        raise ValueError("IMMUTABLE_NATIVE_ENTRY_EVIDENCE_UNAVAILABLE")
    if identity(evidence["quote"]) != ident:
        raise ValueError("ENTRY_IDENTITY_MISMATCH")
    if not opened <= stamp(evidence["clocks"]["intent_at"]) <= stamp(evidence["clocks"]["entry_fill_committed_at"]):
        raise ValueError("ENTRY_LEDGER_CLOCK_MISMATCH")
    exit_policy = features.get("exit_policy") or evidence["inputs_used"].get("exit_policy") or {}
    if exit_policy.get("mode") != "SIMULATED" or not exit_policy.get("end_of_day"):
        raise ValueError("FACTUAL_EOD_POLICY_UNAVAILABLE")
    if settings["eod_policy"] != "FORCE_CLOSE" or not session_policy.close_at_eod:
        raise ValueError("FACTUAL_EOD_POLICY_UNSUPPORTED")
    if not settings["intraday_fee_rebate"]:
        raise ValueError("SHARED_REPLAY_REBATE_CONFIGURATION_UNSUPPORTED")
    if not session_policy.supports(row):
        raise ValueError("FACTUAL_SESSION_NOT_SUPPORTED")
    _, close = session_policy.bounds(opened, row)
    eod = stamp(close - timedelta(minutes=session_policy.exit_minutes))
    if eod <= at:
        raise ValueError("REGISTRATION_AFTER_FACTUAL_EOD")
    price = number(row["entry_price"], positive=True)
    stop, target = number(row["stop_price"], positive=True), number(row["target_price"], positive=True)
    if not stop < price < target:
        raise ValueError("FACTUAL_ENTRY_LEVELS_INVALID")
    if (number(exit_policy.get("stop_loss_price"), positive=True) != stop or
            number(exit_policy.get("take_profit_price"), positive=True) != target):
        raise ValueError("FACTUAL_EXIT_POLICY_CONTRADICTION")
    hold = exit_policy.get("max_hold_minutes")
    if isinstance(hold, bool) or hold is None or number(hold, positive=True) != int(hold):
        raise ValueError("FACTUAL_MAX_HOLD_UNAVAILABLE")
    # The live supervisor takes the effective minimum for scalping.
    hold = min(int(hold), settings["max_hold_minutes"])
    if features.get("execution_style") == "SCALPING_PAPER":
        limit = features.get("scalping_max_hold_minutes")
        if limit is None or isinstance(limit, bool) or number(limit, positive=True) != int(limit):
            raise ValueError("FACTUAL_SCALPING_MAX_HOLD_UNAVAILABLE")
        hold = min(hold, int(limit))
    entry = {key: row[key] for key in ("symbol", "asset_class", "settlement", "currency", "market",
                                     "opened_at", "entry_price", "quantity")}
    entry.update(id=row["paper_id"], paper_id=row["paper_id"],
                 contract_cash_multiplier=str(number(features.get("contract_cash_multiplier", 1), positive=True)),
                 quantity_step=str(number(features.get("contract_quantity_step", 1), positive=True)),
                 score=evidence["score"], strategy_id=evidence["strategy_id"],
                 decision_at=evidence["clocks"]["decision_at"], intent_at=evidence["clocks"]["intent_at"])
    if number(entry["contract_cash_multiplier"]) != 1:
        raise ValueError("EQUITY_CASH_MULTIPLIER_UNVERIFIED")
    expected_entry = (number(evidence["quote"]["ask"], positive=True)*(1+number(settings["slippage"]))).quantize(Decimal(".0001"))
    if expected_entry != price:
        raise ValueError("EXECUTED_ENTRY_COST_ANCHOR_MISMATCH")
    if number(row.get("entry_cost"), nonnegative=True) != ledger_leg_cost(price, entry["quantity"], ident[1]):
        raise ValueError("FACTUAL_ENTRY_COST_AUTHORITY_MISMATCH")
    fees = paper_fee_model(ident[1])
    baseline = ExitPolicy("FACTUAL_BASELINE", 1-stop/price, target/price-1, hold*60)
    volatility, missing = _volatility(checkpoint["history"], entry, entry["decision_at"])
    policies, hypotheses, fingerprint = [baseline], {}, digest(asdict(baseline))
    if volatility:
        preregistration = preregister_exit_variants(baseline, as_of=at, eod_at=eod,
            volatility=volatility["value"], volatility_available_at=volatility["available_at"],
            horizon_seconds=volatility["horizon_seconds"], **HYPOTHESES)
        policies, hypotheses, fingerprint = (preregistration["policies"], preregistration["hypotheses"],
                                             preregistration["configuration_fingerprint"])
    kwargs = {"eod_at": eod.isoformat(), "session_close_at": stamp(close).isoformat(),
              "participation": settings["participation"], "slippage": settings["slippage"],
              "max_age_seconds": settings["max_age_seconds"]}
    model = _model(checkpoint, entry, entry["decision_at"])
    economics = shadow_economics(evidence["quote"], decision_at=entry["decision_at"], eod_at=eod,
        quantity=entry["quantity"], multiplier=entry["contract_cash_multiplier"], model=model, fees=fees,
        participation=settings["participation"], entry_slippage=settings["slippage"],
        exit_slippage=settings["slippage"], max_age_seconds=settings["max_age_seconds"])
    record = {"entry": entry, "registered_at": at.isoformat(), "baseline": _json(asdict(baseline)),
              "fees": _json(asdict(fees)), "replay_kwargs": kwargs, "policies": {}, "path_observations": 0,
              "path_sha256": digest([]), "last_book_at": None, "volatility": volatility,
              "unverified_reasons": [missing] if missing else [], "economics_shadow": _json(economics),
              "economics_input_sha256": digest(evidence["quote"]),
              "entry_evidence_sha256": evidence["payload_sha256"], "native_clocks": evidence["clocks"],
              "entry_configuration_fingerprint": evidence["configuration_fingerprint"],
              "configuration_fingerprint": fingerprint, "hypotheses": hypotheses,
              "score_calibration_oos": "NO_VERIFICADO", "labels": {}, "last_rejection": None}
    for policy in policies:
        definition = _json(asdict(policy))
        laboratory = _restore(entry, definition, record["fees"], kwargs)
        record["policies"][policy.name] = {"definition": definition, "state": _freeze_replay(laboratory),
            "sampled_entry_level_touches": {"target_at": None, "stop_at": None}}
    return record


def _retain_label(record, book, at):
    opened, eod = stamp(record["entry"]["opened_at"]), stamp(record["replay_kwargs"]["eod_at"])
    for name, target in (("eod", eod), ("movement", opened + timedelta(seconds=MODEL_HORIZON_SECONDS))):
        label = record["labels"].setdefault(name, {"target_at": target.isoformat(), "low": None, "high": None,
                                                  "endpoint": None, "observations": 0})
        received = stamp(book["observed_at"])
        if opened < received <= target:
            label["observations"] += 1
            for key, operation in (("low", min), ("high", max)):
                old = label[key]
                if old is None or operation(number(old["bid"]), number(book["bid"])) == number(book["bid"]):
                    label[key] = book
        if target <= received <= target + timedelta(seconds=120) and label["endpoint"] is None:
            label["endpoint"] = book


def _advance(record, books, at, rejections):
    registration = stamp(record["registered_at"])
    for book in books:
        if identity(book) != identity(record["entry"]):
            continue
        if stamp(book["observed_at"]) < registration:
            rejections["PRE_REGISTRATION_PATH_NOT_RECONSTRUCTED"] += 1
            continue
        source_at = stamp(book["book_at"])
        if record["last_book_at"] and source_at <= stamp(record["last_book_at"]):
            rejections["DUPLICATE_OR_REVERSED_NATIVE_BOOK_TIMESTAMP"] += 1
            continue
        error = usable_book(book, at, max_age_seconds=record["replay_kwargs"]["max_age_seconds"])
        if error or source_at < stamp(record["entry"]["opened_at"]):
            record["last_rejection"] = error or "book_before_entry"
            rejections[(error or "book_before_entry").upper()] += 1
            continue
        record["last_book_at"] = source_at.isoformat()
        record["path_observations"] += 1
        record["path_sha256"] = digest([record["path_sha256"], {"as_of": at.isoformat(), "book": book}])
        record["last_rejection"] = None
        _retain_label(record, book, at)
        for policy in record["policies"].values():
            laboratory = _restore(record["entry"], policy["definition"], record["fees"], record["replay_kwargs"], policy["state"])
            laboratory.advance(book, as_of=at)
            policy["state"] = _freeze_replay(laboratory)
            if stamp(book["observed_at"]) < stamp(record["replay_kwargs"]["eod_at"]):
                touches, price = policy["sampled_entry_level_touches"], number(record["entry"]["entry_price"])
                bid = number(book["bid"])
                if bid >= price*(1+number(policy["definition"]["target_fraction"])):
                    touches["target_at"] = touches["target_at"] or at.isoformat()
                if bid <= price*(1-number(policy["definition"]["stop_fraction"])):
                    touches["stop_at"] = touches["stop_at"] or at.isoformat()
    for policy in record["policies"].values():
        laboratory = _restore(record["entry"], policy["definition"], record["fees"], record["replay_kwargs"], policy["state"])
        laboratory.advance(None, as_of=at)
        policy["state"] = _freeze_replay(laboratory)


def _forward_label(record, name, at):
    tracking = record["labels"].get(name)
    if not tracking:
        return {"status": "NO_VERIFICADO", "censored": True, "reason": "PROSPECTIVE_PATH_UNAVAILABLE"}
    entry = record["entry"]
    horizon = int((stamp(tracking["target_at"])-stamp(entry["opened_at"])).total_seconds())
    distinct = {digest(book): book for book in (tracking["low"], tracking["high"], tracking["endpoint"]) if book}
    books = sorted(distinct.values(), key=lambda book: stamp(book["observed_at"]))
    label = forward_labels(dict(entry, decision_at=entry["opened_at"]), books, [horizon], as_of=at)[0]
    if record.get("path_censoring"):
        label.update(status="NO_VERIFICADO", reason=record["path_censoring"])
    # Outcomes start at registration. Retaining extrema is sufficient for the
    # shared label math and bounded storage, not a reconstructed continuous path.
    return _json(label) | {"censored": label["status"] != "MEDIDO", "registered_at": record["registered_at"],
        "observation_basis": "DISTINCT_NATIVE_BOOKS_OBSERVED_PROSPECTIVELY_AFTER_REGISTRATION",
        "full_entry_to_eod_continuous_coverage": "NO_VERIFICADO",
        "path_observations": tracking["observations"], "score_calibration_oos": "NO_VERIFICADO"}


def _summary(record, at):
    label = _forward_label(record, "eod", at)
    variants = []
    for name, policy in record["policies"].items():
        laboratory = _restore(record["entry"], policy["definition"], record["fees"], record["replay_kwargs"], policy["state"])
        result = laboratory.result()
        variants.append({"policy": name, "definition": policy["definition"], "input_sha256": record["path_sha256"],
            "result": _json(result), "net_cost_basis": "shared expected_round_trip_cost; execution prices embed spread/slippage",
            "sampled_entry_level_touches": policy["sampled_entry_level_touches"] |
                {"coverage": label["status"], "continuous_hit_probability": "NO_VERIFICADO"}})
    return {key: record[key] for key in ("entry", "registered_at", "baseline", "volatility",
        "unverified_reasons", "economics_shadow", "economics_input_sha256", "native_clocks",
        "entry_evidence_sha256", "entry_configuration_fingerprint", "configuration_fingerprint",
        "hypotheses", "path_observations", "path_sha256", "last_rejection")} | {
            "forward_label": label, "movement_label": _forward_label(record, "movement", at), "variants": variants}


def _cohorts(records):
    cohorts = {}
    for record in records:
        entry, label = record["entry"], record["forward_label"]
        hour = stamp(entry["opened_at"]).hour
        for variant in record["variants"]:
            key = (entry["asset_class"], entry["currency"], hour, variant["policy"])
            cohort = cohorts.setdefault(key, {"family": key[0], "currency": key[1], "entry_hour_utc": hour,
                "policy": key[3], "entries": 0, "complete_eod_labels": 0, "censored_entries": 0,
                "target_touches_complete": 0, "stop_touches_complete": 0, "net": Decimal(0),
                "mfe_observed": [], "mae_observed": []})
            cohort["entries"] += 1
            cohort["net"] += number(variant["result"]["net"])
            measured = label["status"] == "MEDIDO"
            cohort["complete_eod_labels"] += int(measured)
            cohort["censored_entries"] += int(not measured)
            if measured:
                touches = variant["sampled_entry_level_touches"]
                cohort["target_touches_complete"] += int(touches["target_at"] is not None)
                cohort["stop_touches_complete"] += int(touches["stop_at"] is not None)
                cohort["mfe_observed"].append(label["mfe_observed"])
                cohort["mae_observed"].append(label["mae_observed"])
    for cohort in cohorts.values():
        n = cohort["complete_eod_labels"]
        cohort["sampled_target_touch_fraction"] = cohort["target_touches_complete"]/n if n else None
        cohort["sampled_stop_touch_fraction"] = cohort["stop_touches_complete"]/n if n else None
        cohort["continuous_hit_probability"] = "NO_VERIFICADO"
    return _json(list(cohorts.values()))


def evaluate_runtime_lab(database, *, as_of, previous=None, row_limit=200, runtime_config=None):
    """Return a nonbinding SHADOW report and bounded, JSON-safe checkpoint.

    Caller persists checkpoint atomically under its own exclusive evidence
    writer. Source failures do not advance cursors. Checkpoint/source/config
    incompatibility starts a fresh current-tail watermark, never a backfill.
    """
    at = stamp(as_of)
    if isinstance(row_limit, bool) or not isinstance(row_limit, int) or not 1 <= row_limit <= 500:
        raise ValueError("INVALID_SHADOW_LAB_READ_BUDGET")
    settings, session_policy = _settings(runtime_config)
    fingerprint = digest({"schema": SCHEMA, "settings": settings, "hypotheses": HYPOTHESES,
        "fees": {family: asdict(paper_fee_model(family)) for family in ("ACCIONES", "CEDEARS", "ETFS")},
        "limits": [row_limit, MAX_ACTIVE, MAX_ARCHIVED, MAX_IDENTITIES, HISTORY_POINTS, MODEL_HORIZON_SECONDS, MIN_MODEL_LABELS]})
    invalidated = None
    if previous:
        if len(canonical(previous).encode()) > MAX_CHECKPOINT_BYTES:
            invalidated = "CHECKPOINT_CAPACITY_EXCEEDED"
        elif previous.get("schema") != SCHEMA or previous.get("checkpoint_sha256") != _checkpoint_hash(previous):
            invalidated = "CHECKPOINT_SCHEMA_OR_DIGEST_INVALID"
        elif previous.get("configuration_fingerprint") != fingerprint:
            invalidated = "CONFIGURATION_CHANGED_CHECKPOINT_INVALIDATED"
        elif stamp(previous["last_as_of"]) > at:
            raise ValueError("SHADOW_LAB_TIME_REVERSED")
    prior = None if invalidated else previous
    source_key, tails, rows, existing, truncated = _read(database, at, prior, row_limit)
    if not rows:
        if prior and (source_key != prior.get("source_key") or tails != prior.get("cursors")):
            invalidated = "SOURCE_CHANGED_OR_CURSORS_REVERSED"
        checkpoint = {"schema": SCHEMA, "source_key": source_key, "started_at": at.isoformat(),
            "last_as_of": at.isoformat(), "configuration_fingerprint": fingerprint, "cursors": tails,
            "history": {}, "active": {}, "archive": [], "pending_positions": {}, "pending_evidence": {},
            "retired_entries": 0, "dropped_entries": 0, "existing_open_entries_unverified": existing}
        report = {"status": "START_AT_CURRENT_TAIL", "unverified_reason": "EXISTING_ENTRIES_NOT_RECONSTRUCTED",
                  "existing_open_entries_unverified": existing, "entries": [], "entry_hour_cohorts": []}
    else:
        checkpoint = _json(prior)
        rejections, registered = Counter(), 0
        for table, batch in rows.items():
            for row in batch:
                checkpoint["cursors"][table] = row["_rowid"]
                if row.get("_oversize") or len(canonical(row).encode()) > MAX_ROW_BYTES:
                    rejections["SOURCE_ROW_OVERSIZE"] += 1
                    continue
                if table == "decision_evidence_snapshots":
                    try:
                        evidence = _native_evidence(row, at)
                        if evidence and stamp(evidence["clocks"]["intent_at"]) > stamp(checkpoint["started_at"]):
                            checkpoint["pending_evidence"][evidence["paper_id"]] = evidence
                    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                        rejections[str(exc) if isinstance(exc, ValueError) else "IMMUTABLE_ENTRY_EVIDENCE_INVALID"] += 1
                elif table == "paper_positions":
                    if str(row.get("asset_class")) not in {"ACCIONES", "CEDEARS", "ETFS"}:
                        rejections["SPECIALIZED_LIFECYCLE_OUTSIDE_EXIT_LAB"] += 1
                        continue
                    try:
                        if not stamp(checkpoint["started_at"]) < stamp(row["opened_at"]) <= at:
                            raise ValueError("ENTRY_BEFORE_WATERMARK_OR_IN_FUTURE")
                        checkpoint["pending_positions"][row["paper_id"]] = row
                    except (ValueError, TypeError, KeyError):
                        rejections["ENTRY_BEFORE_WATERMARK_OR_IN_FUTURE"] += 1
        # Register before adding the new quote batch to history. Volatility can
        # therefore only use evidence previously available to the worker.
        for paper_id, row in list(checkpoint["pending_positions"].items()):
            if paper_id in checkpoint["active"]:
                del checkpoint["pending_positions"][paper_id]
                continue
            if len(checkpoint["active"]) >= MAX_ACTIVE:
                rejections["ACTIVE_LAB_CAPACITY_CENSORED"] += 1
                checkpoint["dropped_entries"] += 1
                del checkpoint["pending_positions"][paper_id]
                continue
            try:
                checkpoint["active"][paper_id] = _register(row, checkpoint["pending_evidence"].get(paper_id),
                                                           checkpoint, settings, session_policy, at)
                registered += 1
                del checkpoint["pending_positions"][paper_id]
                checkpoint["pending_evidence"].pop(paper_id, None)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "FACTUAL_ENTRY_INPUTS_INVALID"
                rejections[reason] += 1
                # Gate persistence can lag the ledger transaction by one tick.
                # Missing immutable evidence may retry within the bounded window.
                if reason != "IMMUTABLE_NATIVE_ENTRY_EVIDENCE_UNAVAILABLE" or (at-stamp(row["opened_at"])).total_seconds() > 120:
                    checkpoint["dropped_entries"] += 1
                    del checkpoint["pending_positions"][paper_id]
        books = []
        for row in rows["market_snapshots"]:
            if row.get("_oversize"):
                continue
            try:
                book = _book(row)
                ident = identity(book)
                if ident[1] not in {"ACCIONES", "CEDEARS", "ETFS"}:
                    continue
                error = usable_book(book, at, max_age_seconds=settings["max_age_seconds"])
                if error:
                    rejections[error.upper()] += 1
                    continue
                books.append(book)
            except (ValueError, TypeError, KeyError):
                rejections["INVALID_QUOTE"] += 1
        books.sort(key=lambda book: (stamp(book["observed_at"]), stamp(book["book_at"])))
        for paper_id, record in list(checkpoint["active"].items()):
            if truncated:
                # A bounded read may miss intermediate clocks once the backlog
                # ages. Never use that incomplete path to claim full labels or
                # an empirical touch probability denominator.
                record["path_censoring"] = "SOURCE_READ_TRUNCATED_PATH_CENSORED"
            _advance(record, books, at, rejections)
            if at > stamp(record["replay_kwargs"]["eod_at"]) + timedelta(seconds=120):
                checkpoint["archive"].append(_summary(record, at))
                del checkpoint["active"][paper_id]
                checkpoint["retired_entries"] += 1
        checkpoint["archive"] = checkpoint["archive"][-MAX_ARCHIVED:]
        for book in books:
            key = digest(identity(book))
            history = checkpoint["history"].setdefault(key, [])
            if history and stamp(book["book_at"]) <= stamp(history[-1]["book"]["book_at"]):
                continue
            history.append({"available_at": at.isoformat(), "book": book})
            checkpoint["history"][key] = history[-HISTORY_POINTS:]
        # Old inputs never train a new session; no historical refill is attempted.
        checkpoint["history"] = {key: value for key, value in checkpoint["history"].items()
            if value and (at-stamp(value[-1]["available_at"])).total_seconds() <= 5400}
        ordered = sorted(checkpoint["history"], key=lambda key: checkpoint["history"][key][-1]["available_at"], reverse=True)
        checkpoint["history"] = {key: checkpoint["history"][key] for key in ordered[:MAX_IDENTITIES]}
        for name in ("pending_positions", "pending_evidence"):
            while len(checkpoint[name]) > MAX_ACTIVE*2:
                del checkpoint[name][next(iter(checkpoint[name]))]
                rejections["PENDING_ENTRY_CAPACITY_CENSORED"] += 1
        entries = checkpoint["archive"] + [_summary(record, at) for record in checkpoint["active"].values()]
        report = {"status": "SHADOW_RUNTIME_EVALUATED", "new_registrations": registered,
                  "active_entries": len(checkpoint["active"]), "retired_entries": checkpoint["retired_entries"],
                  "dropped_entries": checkpoint["dropped_entries"], "rejections": dict(rejections),
                  "entries": entries, "entry_hour_cohorts": _cohorts(entries)}
        checkpoint["last_as_of"] = at.isoformat()
    checkpoint["checkpoint_sha256"] = _checkpoint_hash(checkpoint)
    if len(canonical(checkpoint).encode()) > MAX_CHECKPOINT_BYTES:
        raise ValueError("SHADOW_LAB_CHECKPOINT_CAPACITY_EXCEEDED")
    report.update(schema=SCHEMA, as_of=at.isoformat(), mode="SHADOW", decision_effect="NONE",
                  real_orders_sent=0, real_order_routes=[], real_routes="NOT_CALLED",
                  source_database_effect="READ_ONLY", legacy_history_backfill=False,
                  checkpoint_sha256=checkpoint["checkpoint_sha256"], configuration_fingerprint=fingerprint,
                  checkpoint_invalidation=invalidated, source_read_truncated=truncated,
                  source_query_budget_seconds=QUERY_SECONDS, row_limit_per_table=row_limit,
                  economic_edge_validated=False, score_calibration_oos="NO_VERIFICADO",
                  parameter_promotion=False, factual_exit_policy_effect="NONE", provider_requests=0)
    report["cohort_scope"] = {"basis": "bounded prospective registered entries only", "maximum_active": MAX_ACTIVE,
        "maximum_archive": MAX_ARCHIVED, "archived_entries_retained": len(checkpoint["archive"]),
        "archive_truncated": checkpoint["retired_entries"] > len(checkpoint["archive"])}
    return report, checkpoint
