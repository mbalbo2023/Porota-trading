"""Read-only evidence reports. Detailed output belongs in private/runtime artifacts."""
import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import stamp
from .metrics import decision_funnel, exit_latencies, trade_metrics
from .costs import realized_round_trip_cost
from .scanner import ScanBudget


def evidence_report(path, *, event_limit=10000):
    if not 1 <= event_limit <= 10000:
        raise ValueError("BOUNDED_REPORT_REQUIRED")
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=.005)) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        c.execute("PRAGMA busy_timeout=5")
        c.execute("BEGIN")
        total = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (event_limit,)).fetchall()
        probes = [dict(r) for r in c.execute("SELECT * FROM exit_probes ORDER BY at DESC LIMIT 1000")]
        state = c.execute("SELECT value_json FROM state WHERE key='last_capture'").fetchone()
        c.rollback()
    snapshots, fills, cycles = [], [], []
    for row in reversed(rows):
        if hashlib.sha256(row["payload_json"].encode()).hexdigest() != row["payload_sha256"]:
            raise ValueError("EVIDENCE_HASH_MISMATCH")
        payload = json.loads(row["payload_json"])
        if row["source_table"] == "decision_evidence_snapshots":
            snapshots.append(json.loads(payload["payload_json"]))
        elif row["source_table"] == "paper_fills":
            fills.append(payload)
        elif row["source_table"] == "universe_cycle_metrics":
            cycles.append(payload)
    funnel = decision_funnel(snapshots)
    by_position = defaultdict(list)
    for fill in fills:
        by_position[fill["paper_id"]].append(fill)
    trades, unverified = [], []
    for paper_id, legs in by_position.items():
        closed = [f for f in legs if f["side"] == "SELL_SIMULATED" and f.get("position_status") == "CLOSED"]
        if not closed:
            continue
        final = max(closed, key=lambda f: stamp(f["filled_at"]))
        features = json.loads(final.get("features_json") or "{}")
        p = {k: final.get(k) for k in ("paper_id", "symbol", "asset_class", "settlement", "currency", "market",
                                      "entry_price", "entry_cost", "exit_cost", "gross_pnl", "net_pnl", "close_reason", "closed_at")}
        p["quantity"] = final["position_quantity"]
        p["contract_cash_multiplier"] = features.get("contract_cash_multiplier", 1 if p["asset_class"] in {"ACCIONES", "CEDEARS", "ETFS"} else None)
        p["strategy_version"] = final.get("strategy_version", "NO_VERIFICADO")
        p["strategy_id"] = features.get("performance_lineage", {}).get("strategy_id", "NO_VERIFICADO")
        buys = [f for f in legs if f["side"] == "BUY_SIMULATED"]
        p["opened_at"] = min((f["filled_at"] for f in buys), default=None)
        try:
            if not p["opened_at"]:
                raise ValueError("BUY_FILL_NOT_CAPTURED")
            realized_round_trip_cost(p, legs)
        except ValueError as exc:
            unverified.append({"paper_id": paper_id, "status": "NO_VERIFICADO", "reason": str(exc)})
            continue
        trades.append(p)
    reconciled = {p["paper_id"]: p for p in trades}
    for row in funnel["lineage"]:
        paper_id = row.get("paper_id")
        legs = by_position.get(paper_id, [])
        buys = [f for f in legs if f["side"] == "BUY_SIMULATED"]
        sells = [f for f in legs if f["side"] == "SELL_SIMULATED"]
        row["entry_ledger_at"] = min((f["filled_at"] for f in buys), default=None)
        row["first_exit_fill_at"] = min((f["filled_at"] for f in sells), default=None)
        p = reconciled.get(paper_id)
        row["closed_at"] = p["closed_at"] if p else None
        row["realized_pnl"] = {k: p[k] for k in ("currency", "gross_pnl", "net_pnl")} if p else None
        row["exit_pnl_status"] = "LEDGER_RECONCILED" if p else "NO_VERIFICADO"
    cohorts = defaultdict(list)
    for p in trades:
        day = stamp(p["closed_at"]).astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
        for dimension, key in (("day", day.date().isoformat()), ("week", str(day.date().isocalendar()[:2])),
                               ("strategy", p["strategy_version"]), ("symbol", p["symbol"]), ("family", p["asset_class"]),
                               ("version", p["strategy_version"]), ("strategy_id", p["strategy_id"]),
                               ("entry_hour", str(stamp(p["opened_at"]).astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).hour)), ("exit_reason", p["close_reason"])):
            cohorts[(dimension, key, p["currency"])].append(p)
    scanner = {"status": "NO_VERIFICADO"}
    if cycles:
        latest = cycles[-1]
        text = latest.get("detail", "")
        offset = text.find("{")
        if offset >= 0:
            try:
                budget = json.loads(text[offset:])
                scanner = ScanBudget(int(budget["rotation_pool"]), int(budget["rotation_pool"]),
                                     int(budget["rotation_slots"]), float(budget["estimated_cycle_seconds"]),
                                     int(budget["required_samples"]), float(budget["window_minutes"])*60).report()
                scanner["scope"] = "ROTATION_ONLY"
                scanner["factual_scanner_authority"] = "LEGACY_NO_ACTIVE_PLAN"
                if budget.get("active_plan"):
                    scanner = {"status": "FEASIBLE" if budget["feasible"] else "INFEASIBLE",
                               "scope": "ACTIVE_BASKET_OPENING_GATE",
                               "active_budget": budget["active_plan"]["active_budget"],
                               "full_catalog_budget": budget["active_plan"]["full_catalog_budget"],
                               "catalog_effect": "NONE", "signal_ready": "NO_VERIFICADO",
                               "economically_actionable": "NO_VERIFICADO"}
            except (ValueError, KeyError, TypeError):
                scanner = {"status": "NO_VERIFICADO", "reason": "scanner_budget_unparseable"}
    return {"schema": "rc6.performance-report.v1", "mode": "SHADOW", "decision_effect": "NONE",
            "real_order_routes": [], "generated_at": datetime.now(timezone.utc).isoformat(),
            "selection": {"captured_events": total, "selected_events": len(rows), "limit": event_limit,
                          "truncated": total > event_limit, "legacy_history_backfill": False},
            "last_capture": json.loads(state[0]) if state else None,
            "funnel": funnel, "scanner": scanner,
            "exit_latency": exit_latencies(probes, fills),
            "economic_cohorts": [{"dimension": d, "key": key, "currency": cur, **trade_metrics(ts)}
                                 for (d, key, cur), ts in sorted(cohorts.items())],
            "unverified_positions": unverified, "economic_edge_validated": False,
            "forward_signal_evaluation": {"status": "NO_VERIFICADO",
                                          "reason": "requires exact decision clock and frozen out-of-sample labels"},
            "shadow_gate": {"status": "NO_VERIFICADO", "reason": "expected_move_not_calibrated"}}
