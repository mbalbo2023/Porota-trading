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
