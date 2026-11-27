"""SHADOW capacity from the production scanner's completed IOL cycles."""
from datetime import timedelta
import json
import math
from re import fullmatch

from co_market_sessions_hf6 import byma_paper_spot_open
from rc6_iol_intraday import FRESH_SECONDS
from .common import digest, stamp


SCHEMA = "rc6.iol-scanner-capacity.v1"
CADENCE_SECONDS = FRESH_SECONDS
_CYCLE_COUNT = 3


def paired_sample(quote, identity):
    evidence = quote.iol_trade_evidence or {}
    if (quote.metadata_source != "IOL_REST" or evidence.get("identity") != list(identity)
            or quote.opening_block_reason or quote.time_error(quote.observed_at, require_trade=True)
            or not 0 < quote.bid <= quote.ask or min(quote.bid_size, quote.ask_size) <= 0):
        return None
    return [list(identity), quote.trade_at, quote.book_at, quote.observed_at,
            evidence.get("gateway_generation")]


def cycle_evidence(metrics, pairs, budget, *, complete):
    metrics = metrics or {}
    return {"schema": SCHEMA, "complete": complete,
            "provider": metrics.get("provider"), "authenticated": metrics.get("authenticated") is True,
            "subscribed": metrics.get("trade_stream_state") == "SUBSCRIBED",
            "generation": metrics.get("session_generation"), "wire_attempts": metrics.get("wire_attempts"),
            "rate_limited_attempts": metrics.get("rate_limited_attempts"),
            "pairs": pairs, "budget": budget}


def read_cycles(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='universe_cycle_metrics'").fetchone():
        return []
    rows = connection.execute("""SELECT started_at,finished_at,detail
        FROM universe_cycle_metrics ORDER BY id DESC LIMIT ?""", (_CYCLE_COUNT,)).fetchall()
    cycles = []
    for started, finished, detail in reversed(rows):
        try:
            item = json.loads(detail[detail.index("{"):]).get("iol_capacity_cycle", {})
            cycles.append({**item, "started_at": started, "finished_at": finished})
        except (ValueError, TypeError, AttributeError):
            cycles.append({})
    return cycles


def _wire_count(value):
    if type(value) is not int or value < 0:
        raise ValueError("IOL_CAPACITY_WIRE_COUNTER_INVALID")
    return value


def _configuration(cycle):
    generation = cycle["generation"]
    if (cycle["schema"] != SCHEMA or cycle["provider"] != "IOL_REST"
            or not isinstance(generation, str) or not fullmatch(r"[0-9a-f]{64}", generation)):
        raise ValueError("IOL_CAPACITY_GATEWAY_UNVERIFIED")
    budget = cycle["budget"]
    calls = _wire_count(budget["calls_per_minute"])
    limit = _wire_count(budget["scanner_batch_limit"])
    cost = _wire_count(budget["scanner_calls_per_instrument"])
    batch_count = _wire_count(budget["batches_per_minute"])
    exit_calls = _wire_count(budget["planned_exit_calls"])
    if not (cost == 3 and batch_count > 0 and exit_calls > 0
            and 0 < limit and limit * cost * batch_count + exit_calls + 1 <= calls):
        raise ValueError("IOL_CAPACITY_BUDGET_UNVERIFIED")
    return {"schema": SCHEMA, "generation": generation, "budget": budget}


def safe_iol_capacity(cycles, *, as_of):
    """Use only distinct paired receipts under one budget in the current session."""
    now = stamp(as_of)
    result = {"status": "NO_VERIFICADO", "reason_codes": [], "safe_limit": 0,
              "slots_by_endpoint": {}, "shared_global_slots": 0, "mode": "SHADOW",
              "source": "IOL_REST", "configuration_fingerprint": digest({"schema": SCHEMA}),
              "production_limit_modified": False, "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
    try:
        if not byma_paper_spot_open(now):
            raise ValueError("IOL_CAPACITY_SESSION_NOT_OPEN")
        if len(cycles) != _CYCLE_COUNT:
            raise ValueError("IOL_CAPACITY_WARMING")
        config = _configuration(cycles[-1])
        generation = config["generation"]
        limit = config["budget"]["scanner_batch_limit"]
        previous_start, previous_end, previous_wire = None, None, None
        no_rate_limits = _wire_count(cycles[0]["rate_limited_attempts"])
        tracked = None
        max_gap = 0
        for cycle in cycles:
            if (_configuration(cycle) != config or cycle["complete"] is not True
                    or cycle["authenticated"] is not True or cycle["subscribed"] is not True):
                raise ValueError("IOL_CAPACITY_CYCLE_UNVERIFIED")
            start, end = stamp(cycle["started_at"]), stamp(cycle["finished_at"])
            if (not start <= end <= now or not byma_paper_spot_open(start)
                    or not byma_paper_spot_open(end) or (end-start).total_seconds() > CADENCE_SECONDS
                    or not 0 <= (now-start).total_seconds() <= (_CYCLE_COUNT + 1) * CADENCE_SECONDS):
                raise ValueError("IOL_CAPACITY_STALE_OR_FUTURE")
            wire = _wire_count(cycle["wire_attempts"])
            if _wire_count(cycle["rate_limited_attempts"]) != no_rate_limits:
                raise ValueError("IOL_CAPACITY_RATE_LIMITED")
            pairs = {}
            for identity, trade, book, received, owner in cycle["pairs"]:
                key = tuple(identity)
                trade, book, received = stamp(trade), stamp(book), stamp(received)
                if (len(key) != 5 or key[2] != "BYMA" or key in pairs or owner != generation
                        or not all(isinstance(part, str) and part for part in key)
                        or not start <= received <= end
                        or any(not 0 <= (received-clock).total_seconds() <= FRESH_SECONDS for clock in (trade, book))):
                    raise ValueError("IOL_CAPACITY_PAIR_UNVERIFIED")
                pairs[key] = (trade, book, received)
            if len(pairs) > limit:
                raise ValueError("IOL_CAPACITY_BUDGET_UNVERIFIED")
            if tracked is None:
                tracked = pairs
            else:
                step = (start - previous_start).total_seconds()
                if not previous_end <= start or not 0 < step <= CADENCE_SECONDS:
                    raise ValueError("IOL_CAPACITY_CADENCE_UNVERIFIED")
                if wire - previous_wire < 3 * len(pairs):
                    raise ValueError("IOL_CAPACITY_WIRE_COUNTER_INVALID")
                max_gap = max(max_gap, step)
                tracked = {key: row for key, row in pairs.items() if key in tracked
                           and row[0] > tracked[key][0] and row[1] >= tracked[key][1]
                           and 0 < (row[2] - tracked[key][2]).total_seconds() <= CADENCE_SECONDS}
            previous_start, previous_end, previous_wire = start, end, wire
        expires = previous_end + timedelta(seconds=CADENCE_SECONDS)
        if now > expires:
            raise ValueError("IOL_CAPACITY_STALE_OR_FUTURE")
        safe = math.floor(len(tracked) * 0.75)
        if safe < 1:
            raise ValueError("IOL_CAPACITY_INSUFFICIENT_DISTINCT_PAIRS")
        result.update(status="SHADOW_RECOMMENDATION", safe_limit=safe,
                      slots_by_endpoint={name: safe for name in ("current", "book", "intraday")},
                      shared_global_slots=safe, configuration_fingerprint=digest(config),
                      evidence_digest=digest(cycles), generated_at=previous_end.isoformat(),
                      expires_at=expires.isoformat(), completed_cycles=len(cycles),
                      max_cycle_gap_seconds=max_gap, measured_identities=sorted(tracked))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        code = str(error)
        result["reason_codes"] = [code if fullmatch(r"IOL_CAPACITY_[A-Z_]+", code)
                                  else "IOL_CAPACITY_EVIDENCE_INVALID"]
    return result
