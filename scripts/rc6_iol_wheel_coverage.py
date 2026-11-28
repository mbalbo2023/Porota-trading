"""Reconcile the installed IOL wheel's committed selections, quotes and decisions."""

from bisect import bisect_right
from collections import Counter, defaultdict
from contextlib import closing, nullcontext
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import time
from types import SimpleNamespace


START = "2026-11-27T13:30:00+00:00"
END = "2026-11-27T20:00:00+00:00"
DB = Path("/app/data/paper_v17/observer_v17.db")


def read_rows(conn, sql, limit):
    deadline = time.monotonic() + 3
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
    try:
        rows = conn.execute(sql, (START, END, limit + 1)).fetchall()
    finally:
        conn.set_progress_handler(None, 0)
    if len(rows) > limit:
        raise ValueError("WHEEL_EVIDENCE_ROW_LIMIT")
    return rows


def identity(values):
    if (isinstance(values, (list, tuple)) and len(values) == 5
            and all(isinstance(v, str) and re.fullmatch(r"[A-Z0-9.^_()/-]{1,36}", v) for v in values)):
        return tuple(values)
    return None


def instant(value):
    return datetime.fromisoformat(value).timestamp()


def readonly_connection(path):
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def book_and_trade_fresh(row, quote_from_snapshot):
    try:
        quote = quote_from_snapshot(row)
        return (not quote.opening_block_reason and quote.last_kind == "TRADE"
                and not quote.time_error(quote.observed_at, require_trade=True)
                and 0 < quote.bid <= quote.ask and min(quote.bid_size, quote.ask_size) > 0)
    except (TypeError, ValueError, ArithmeticError):
        return False


def public_code(value):
    if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z_0-9]{2,95}", value):
        return value
    return "OTHER_RECORDED_REASON"


def diagnostic_read(read, *args):
    try:
        return read(*args)
    except (sqlite3.Error, AttributeError, KeyError, ValueError, TypeError, ArithmeticError) as exc:
        details = {"state": "NO_VERIFICADO", "error_type": type(exc).__name__}
        trace = exc.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            if code.co_filename == "<string>" or code.co_filename.endswith("rc6_iol_wheel_coverage.py"):
                details[code.co_name] = trace.tb_lineno
            trace = trace.tb_next
        if isinstance(exc, sqlite3.Error):
            details["sqlite_error_name"] = public_code(getattr(exc, "sqlite_errorname", None))
            schema_name = re.fullmatch(r"no such (table|column): ([a-z0-9_.]+)", str(exc))
            if schema_name:
                details["schema_error"] = list(schema_name.groups())
        elif type(exc) is ValueError:
            details["reason"] = public_code(str(exc))
        return details


def diagnose_freshness(conn, cycles, snapshots, close_eligible, quote_from_snapshot):
    turn_rows = read_rows(conn, """SELECT cycle_started_at,symbol FROM symbol_cycle_metrics
        WHERE julianday(cycle_started_at)>=julianday(?) AND julianday(cycle_started_at)<julianday(?)
          AND instr(detail,'IOL REST')>0 ORDER BY id LIMIT ?""", 10000)
    events = read_rows(conn, """SELECT event_type,event_at,detail FROM paper_events
        WHERE event_type IN ('DATA_ERROR','DATA_REJECTED')
          AND julianday(event_at)>=julianday(?) AND julianday(event_at)<julianday(?)
        ORDER BY event_at,id LIMIT ?""", 24000)
    turns = defaultdict(list)
    for row in turn_rows:
        turns[row["cycle_started_at"]].append(row["symbol"])
    planned, attempted, fresh = set(), set(), set()
    slices = []
    holds, coverage, rejects_by_slot = Counter(), Counter(), Counter()
    coverage["symbol_turn_rows_for_window"] = len(turn_rows)
    coverage["reject_rows_for_window"] = len(events)
    errors_by_identity = defaultdict(set)
    for cycle in cycles:
        started = cycle["started_at"]
        try:
            detail = json.loads(cycle["detail"].split("factibilidad=", 1)[1])
            plan = detail["active_plan"]
            if "iol_rest_budget" not in plan and started not in turns:
                coverage["cycles_without_iol_marker"] += 1
                continue
            coverage["iol_plans"] += 1
            if "iol_rest_budget" not in plan:
                coverage["iol_plans_linked_via_symbol_metrics"] += 1
            keys = [identity(item.get("identity")) for item in plan.get("read_scopes", ())]
            keys = list(dict.fromkeys(k for k in keys if k is not None))
            coverage["bound_plan_slots"] += len(keys)
            if len(keys) != cycle["selected_count"]:
                coverage["plans_without_all_exact_scopes"] += 1
            if detail.get("opening_hold_reason"):
                holds[public_code(detail["opening_hold_reason"])] += 1
            begin, end = instant(started), instant(cycle["finished_at"])
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            coverage["unparsed_cycle"] += 1
            continue
        lookup = defaultdict(list)
        for key in keys:
            planned.add(key)
            lookup[key[0]].append(key)
        for symbol in turns[started]:
            candidates = lookup[symbol]
            if len(candidates) == 1:
                attempted.add(candidates[0])
                coverage["exact_symbol_turns"] += 1
            else:
                coverage["unbound_symbol_turns"] += 1
        slices.append((begin, end, lookup))
    starts = [begin for begin, _, _ in slices]
    for event in events:
        if not isinstance(event["detail"], str):
            continue
        name, separator, reason = event["detail"].partition(": ")
        if not separator:
            continue
        at = instant(event["event_at"])
        index = bisect_right(starts, at) - 1
        if index < 0 or at > slices[index][1]:
            continue
        candidates = slices[index][2].get(name, ())
        reason = public_code(reason)
        rejects_by_slot[reason] += 1
        if len(candidates) != 1:
            coverage["unbound_reject_events"] += 1
            continue
        key = candidates[0]
        attempted.add(key)
        errors_by_identity[key].add(reason)

    seen, snapshot_reasons = set(), Counter()
    for row in snapshots:
        key = identity(tuple(row[k] for k in ("symbol", "asset_class", "market", "currency", "settlement")))
        if key is None:
            continue
        seen.add(key)
        if book_and_trade_fresh(row, quote_from_snapshot):
            fresh.add(key)
            continue
        try:
            quote = quote_from_snapshot(row)
            reason = (quote.opening_block_reason
                      or quote.time_error(quote.observed_at, require_trade=True)
                      or "NON_EXECUTABLE_BOOK_DEPTH")
        except (ValueError, TypeError, ArithmeticError):
            reason = "INVALID_SNAPSHOT"
        reason = public_code(reason)
        snapshot_reasons[reason] += 1
        errors_by_identity[key].add(reason)

    family = defaultdict(Counter)
    missing_reasons = defaultdict(Counter)
    for key in sorted(close_eligible):
        result = family[key[1]]
        if key in fresh:
            result["fresh_pair"] += 1
        elif key in seen:
            result["snapshot_without_fresh_pair"] += 1
        elif key in attempted:
            result["turn_without_snapshot"] += 1
        elif key in planned:
            result["planned_without_exact_turn_evidence"] += 1
        else:
            result["absent_from_saved_plans"] += 1
        if key not in fresh:
            reasons = errors_by_identity.get(key, ())
            missing_reasons[key[1]].update(reasons)
    return {"evidence": dict(coverage), "cycle_holds": dict(holds),
            "scanner_rejections": dict(rejects_by_slot),
            "unusable_snapshot_reasons": dict(snapshot_reasons),
            "close_eligible_by_family": {k: dict(v) for k, v in sorted(family.items())},
            "missing_identity_reasons_by_family": {k: dict(v) for k, v in sorted(missing_reasons.items())}}


def family_runtime(conn):
    catalog = [dict(row) for row in conn.execute("""SELECT f.instrument_type AS family,
        count(*) AS known,
        sum(CASE WHEN f.status='AVAILABLE' THEN 1 ELSE 0 END) AS available,
        sum(CASE WHEN f.status='AVAILABLE' AND f.capability LIKE 'READY_PAPER%' THEN 1 ELSE 0 END) AS ready_classification,
        sum(CASE WHEN f.status='AVAILABLE' AND c.status='AVAILABLE' AND c.can_simulate=1
          THEN 1 ELSE 0 END) AS paper_ledger_eligible
        FROM financial_instrument_catalog f LEFT JOIN candidate_identity_v2 c
        USING(ticker,instrument_type,market,currency,settlement)
        GROUP BY f.instrument_type ORDER BY f.instrument_type""")]
    codes = defaultdict(Counter)
    for row in conn.execute("""SELECT instrument_type,detail,count(*) AS n
        FROM candidate_identity_v2 WHERE can_simulate=0 GROUP BY instrument_type,detail"""):
        for token in set(str(row["detail"] or "").split(";")):
            if token:
                codes[row["instrument_type"]][public_code(token)] += row["n"]
    lifecycle = read_rows(conn, """SELECT family,to_state,count(*) AS n FROM paper_family_lifecycle_events
        WHERE julianday(occurred_at)>=julianday(?) AND julianday(occurred_at)<julianday(?)
        GROUP BY 1,2 LIMIT ?""", 80)
    caucion_attempts = read_rows(conn, """SELECT status,code,count(*) AS n FROM paper_caucion_cash_sweep_attempts
        WHERE julianday(evaluated_at)>=julianday(?) AND julianday(evaluated_at)<julianday(?)
        GROUP BY 1,2 LIMIT ?""", 80)
    caucion_placements = read_rows(conn, """SELECT status,count(*) AS n FROM paper_cauciones
        WHERE julianday(opened_at)>=julianday(?) AND julianday(opened_at)<julianday(?)
        GROUP BY 1 LIMIT ?""", 20)
    scalping = read_rows(conn, """SELECT asset_class AS family,action,count(*) AS n FROM scalping_candidates
        WHERE julianday(evaluated_at)>=julianday(?) AND julianday(evaluated_at)<julianday(?)
        GROUP BY 1,2 LIMIT ?""", 80)
    worker = conn.execute("""SELECT heartbeat_at,state,code,last_attempt_at,real_orders_sent
        FROM paper_caucion_cash_sweep_state WHERE id=1""").fetchone()
    return {"catalog": catalog, "catalog_blockers": {k: dict(v.most_common(5)) for k, v in sorted(codes.items())},
            "specialized_lifecycle_events": [dict(r) for r in lifecycle],
            "caucion_placement_attempts": [dict(r) for r in caucion_attempts],
            "caucion_placements": [dict(r) for r in caucion_placements],
            "caucion_worker_checkpoint": dict(worker) if worker else None,
            "scalping_decisions": [dict(r) for r in scalping]}


def audit(conn, quote_from_snapshot, signal_sample_count, required_samples, close_eligible):
    cycles = read_rows(conn, """SELECT started_at, finished_at, eligible_total, selected_count,
        successful_count, failed_count, detail FROM universe_cycle_metrics
        WHERE julianday(started_at)>=julianday(?) AND julianday(started_at)<julianday(?)
        ORDER BY started_at, id LIMIT ?""", 1500)
    snapshots = read_rows(conn, """SELECT * FROM market_snapshots
        WHERE julianday(observed_at)>=julianday(?) AND julianday(observed_at)<julianday(?)
          AND metadata_source='IOL_REST' ORDER BY observed_at, id LIMIT ?""", 18000)
    decisions = read_rows(conn, """SELECT d.symbol,d.decided_at,d.decision_key,d.action,d.reason,
        g.final_result,g.reason AS gate_reason FROM paper_decisions d
        LEFT JOIN trade_gate_evaluations g USING(decision_key)
        WHERE julianday(d.decided_at)>=julianday(?) AND julianday(d.decided_at)<julianday(?)
        ORDER BY d.decided_at, d.id LIMIT ?""", 12000)
    fills = read_rows(conn, """SELECT p.asset_class family,f.side,count(*) AS fills
        FROM paper_fills f JOIN paper_positions p USING(paper_id)
        WHERE julianday(f.filled_at)>=julianday(?) AND julianday(f.filled_at)<julianday(?)
        GROUP BY 1,2 ORDER BY 1,2 LIMIT ?""", 80)
    positions = read_rows(conn, """SELECT symbol,opened_at,closed_at FROM paper_positions
        WHERE julianday(COALESCE(closed_at,'9999-12-31'))>=julianday(?)
          AND julianday(opened_at)<julianday(?) LIMIT ?""", 600)

    keys = defaultdict(lambda: defaultdict(set))
    counts = defaultdict(Counter)
    allocations = []
    wheel = Counter()
    eligible_range = []
    for row in cycles:
        try:
            detail = json.loads(row["detail"].split("factibilidad=", 1)[1])
            plan = detail["active_plan"]
            if "iol_rest_budget" not in plan:
                wheel["other_cycles"] += 1
                continue
            start, finish = instant(row["started_at"]), instant(row["finished_at"])
        except (TypeError, ValueError, IndexError, KeyError, AttributeError):
            wheel["unclassified_cycles"] += 1
            continue
        wheel["iol_cycles"] += 1
        eligible_range.append(row["eligible_total"])
        for field in ("selected_count", "successful_count", "failed_count"):
            wheel[field] += row[field]
        if plan.get("dynamic_selection") is True:
            wheel["dynamic_cycles"] += 1
        observed = detail.get("observation_counts", {})
        if type(observed.get("instruments_dispatched")) is int:
            wheel["dispatched_slots"] += observed["instruments_dispatched"]
            wheel["cycles_with_dispatch_counts"] += 1
        selected = {key for read in plan.get("read_scopes", []) if (key := identity(read.get("identity")))}
        wheel["unbound_selected_slots"] += max(0, row["selected_count"] - len(selected))
        allocations.append((start, finish, selected))
        for key in selected:
            keys[key[1]]["planned"].add(key)
            counts[key[1]]["planned_slots"] += 1

    starts = [start for start, _, _ in allocations]
    quotes = defaultdict(dict)
    fresh_rows = {}
    for row in snapshots:
        key = identity(tuple(row[k] for k in ("symbol", "asset_class", "market", "currency", "settlement")))
        if not key:
            wheel["unbound_snapshots"] += 1
            continue
        at = row["observed_at"]
        keys[key[1]]["observed"].add(key)
        counts[key[1]]["snapshots"] += 1
        fresh = book_and_trade_fresh(row, quote_from_snapshot)
        quotes[(key[0], at)][key] = quotes[(key[0], at)].get(key, False) or fresh
        if fresh:
            keys[key[1]]["fresh"].add(key)
            counts[key[1]]["fresh_snapshots"] += 1
            fresh_rows[(key, at)] = row
            pos = bisect_right(starts, instant(at)) - 1
            if pos < 0 or instant(at) > allocations[pos][1] or key not in allocations[pos][2]:
                keys[key[1]]["fresh_outside_saved_plan"].add(key)
                counts[key[1]]["fresh_outside_saved_plan_snapshots"] += 1

    evaluated_times = set()
    pre_signal_rejections = {}
    buy_results = Counter()
    vetoes = Counter()
    veto_identities = defaultdict(set)
    for row in decisions:
        candidates = quotes.get((row["symbol"], row["decided_at"]), {})
        matched = [(key, is_fresh) for key, is_fresh in candidates.items()
                   if f":{key[0]}:{key[1]}:{key[4]}:{key[3]}:{key[2]}:{row['decided_at'][:16]}:" in row["decision_key"]]
        if len(matched) != 1:
            wheel["decisions_without_exact_iol_snapshot"] += 1
            continue
        key, is_fresh = matched[0]
        family = key[1]
        if ":OBSERVATION_REJECTED:" in row["decision_key"]:
            counts[family]["pre_signal_rejections"] += 1
            pre_signal_rejections[(key, row["decided_at"])] = row["reason"]
            continue
        keys[family]["evaluated"].add(key)
        counts[family]["decisions"] += 1
        evaluated_times.add((key, row["decided_at"]))
        if row["action"] != "BUY":
            continue
        if not is_fresh:
            counts[family]["buy_without_fresh_pair"] += 1
            continue
        result = row["final_result"]
        result = result if result in ("BLOCKED", "OPENED_SIMULATED") else "GATE_NOT_RECORDED"
        buy_results[result] += 1
        counts[family]["fresh_buy"] += 1
        if result == "BLOCKED":
            reason = row["gate_reason"] or ""
            reason = reason if re.fullmatch(r"[A-Z][A-Z_0-9]{2,95}", reason) else "OTHER_RECORDED_GATE"
            vetoes[reason] += 1
            veto_identities[reason].add(key)

    windows = defaultdict(list)
    for row in positions:
        windows[row["symbol"]].append((instant(row["opened_at"]),
            instant(row["closed_at"]) if row["closed_at"] else float("inf")))
    evaluated_minutes = {(key, at[:16]) for key, at in evaluated_times}
    unmatched = Counter()
    remaining = []
    for (key, at), row in sorted(fresh_rows.items()):
        if (key, at) in evaluated_times:
            continue
        counts[key[1]]["fresh_frames_without_saved_evaluation"] += 1
        time_at = instant(at)
        if any(opened <= time_at < closed for opened, closed in windows[key[0]]):
            classification = "POSITION_ALREADY_OPEN"
        elif (key, at[:16]) in evaluated_minutes:
            classification = "IDENTITY_MINUTE_EVALUATED"
        else:
            samples = signal_sample_count(row)
            classification = ("BELOW_SIGNAL_WARMUP" if samples < required_samples
                              else "TECHNICAL_REPLAY_REQUIRED")
            reason = pre_signal_rejections.get((key, at))
            remaining.append({"identity": key, "observed_at": at, "samples": samples,
                              "classification": classification,
                              "pre_signal_reason": reason if reason and
                              re.fullmatch(r"[A-Z][A-Z_0-9]{2,95}", reason) else None})
        unmatched[classification] += 1
    by_family = {}
    for family in sorted(keys.keys() | {k[1] for k in close_eligible}):
        group = keys[family]
        eligible = {key for key in close_eligible if key[1] == family}
        by_family[family] = {**counts[family],
            **{name + "_identities": len(group[name]) for name in
               ("planned", "observed", "fresh", "evaluated", "fresh_outside_saved_plan")},
            "planned_without_fresh_pair": len(group["planned"] - group["fresh"]),
            "close_catalog_eligible": len(eligible),
            "close_catalog_eligible_with_fresh_pair": len(eligible & group["fresh"]),
            "close_catalog_eligible_without_fresh_pair": len(eligible - group["fresh"]),
            "identity_sets": {name: sorted(group[name]) for name in ("planned", "fresh", "evaluated")}}
    return {"status": "RECORDED_SESSION_RECONCILED", "start": START, "end_exclusive": END,
            "cycles": dict(wheel), "scanner_eligible_min": min(eligible_range, default=None),
            "scanner_eligible_max": max(eligible_range, default=None),
            "by_family": by_family, "fresh_buy_outcomes": dict(buy_results),
            "observed_buy_vetoes": dict(vetoes),
            "veto_distinct_identities": {k: len(v) for k, v in veto_identities.items()},
            "spot_paper_fills": [dict(r) for r in fills],
            "fresh_frames_unmatched_to_decisions": dict(unmatched),
            "required_signal_samples": required_samples, "fresh_unmatched_signal_windows": remaining,
            "freshness_rca": diagnostic_read(diagnose_freshness, conn, cycles, snapshots, close_eligible, quote_from_snapshot),
            "family_runtime": diagnostic_read(family_runtime, conn),
            "missed_buy_without_reference": "NOT_IDENTIFIABLE_FROM_UNOBSERVED_PRICES_OR_BOOKS",
            "quote_evaluation_coverage_method": "EXACT_FIVE_PART_IDENTITY_AND_PERSISTED_OBSERVATION_CLOCK"}


if __name__ == "__main__":
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import SIGNAL_MIN_SAMPLES, SIGNAL_WINDOW_MINUTES
    from bu_instrument_catalog import iol_spot_catalog

    read_catalog = SimpleNamespace(connect=lambda: readonly_connection(DB))
    close_eligible = set(iol_spot_catalog(read_catalog, now=datetime.fromisoformat(END)))
    with closing(readonly_connection(DB)) as conn:
        conn.execute("BEGIN")
        obs = dict(conn.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone())
        if obs != {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0}:
            raise ValueError("PAPER_ZERO_UNVERIFIED")

        def samples_at(row):
            quote = PaperStore.quote_from_snapshot(row)
            read_store = SimpleNamespace(connect=lambda: nullcontext(conn))
            return len(PaperStore.signal_prices(read_store, quote, quote.observed_at,
                limit=max(20, SIGNAL_MIN_SAMPLES), window_minutes=SIGNAL_WINDOW_MINUTES))

        report = audit(conn, PaperStore.quote_from_snapshot, samples_at, SIGNAL_MIN_SAMPLES, close_eligible)
    print(json.dumps({"observer": obs, "wheel_coverage": report}, sort_keys=True))
