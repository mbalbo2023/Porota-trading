"""Reconcile already extracted replays; never claim to rerun missing tick paths."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from .common import number, stamp
from .costs import FeeModel, expected_round_trip_cost
from .metrics import ART, audit_master


def extracted_replays(master, folder, *, sessions):
    factual = audit_master(master, sessions=sessions)
    days = {date.fromisoformat(str(day)) for day in sessions}
    trades = [t for t in master["trades"] if t["currency"] == "ARS" and
              stamp(t["closed_at"]).astimezone(ART).date() in days]
    ids = {t["paper_id"] for t in trades}
    baseline = sum((number(t["net_pnl"]) for t in trades), Decimal(0))
    sources, hashes = {}, {}
    for name in ("targets", "grilla"):
        path = Path(folder)/("fuente_replay_"+name+".json")
        raw = path.read_bytes()
        source = json.loads(raw)
        if (source.get("read_only") is not True or source.get("broker_calls_performed") is not False or
                source.get("network_calls_performed") is not False or
                source.get("safety") != {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0}):
            raise ValueError("PAPER_READ_ONLY_REPLAY_REQUIRED")
        sources[name], hashes[name] = source, hashlib.sha256(raw).hexdigest()
    targets, grid, stops = [], [], []
    for section, kind in (("targets", "TP_BRUTO"), ("net_targets", "TP_NETO")):
        for target, details in sorted(sources["targets"][section].items(), key=lambda p: number(p[0])):
            changed = [t for t in details["changed"] if t["paper_id"] in ids]
            if len({t["paper_id"] for t in changed}) != len(changed):
                raise ValueError("DUPLICATE_REPLAY_POSITION")
            delta = sum((number(t["delta_net_pnl"]) for t in changed), Decimal(0))
            targets.append({"kind": kind, "target": target, "n_ars": len(trades),
                            "changed_trades": len(changed), "candidate_net_ars": baseline+delta,
                            "baseline_net_ars": baseline, "delta_ars": delta})
    for profile, details in sorted(sources["grilla"]["details"].items()):
        selected = [t for t in details if t["paper_id"] in ids]
        if len({t["paper_id"] for t in selected}) != len(selected):
            raise ValueError("DUPLICATE_REPLAY_POSITION")
        usable = [t for t in selected if t.get("status") == "REPLAYED" and t.get("net_pnl") is not None]
        grid.append({"profile": profile, "n_ars": len(usable), "not_replayed": len(selected)-len(usable),
                     "missing_positions": len(ids-{t["paper_id"] for t in selected}),
                     "net_ars": sum((number(t["net_pnl"]) for t in usable), Decimal(0)),
                     "wins": sum(number(t["net_pnl"]) > 0 for t in usable)})
    # Explicit historical PAPER sensitivity, not individual account terms.
    fees = FeeModel(Decimal(".006"), Decimal(".0005"), Decimal(".21"), True, True,
                    "ATTACHED_20_SESSION_PAPER_POST_STOP_MODEL")
    for trade in trades:
        if trade["close_reason"] != "STOP_PAPER":
            continue
        observed = trade.get("post_exit_recovery") or {}
        bid = observed.get("max_bid_120m")
        row = {"paper_id": trade["paper_id"], "symbol": trade["symbol"],
               "sample_count": observed.get("observations_used"), "max_bid_observed_120m": bid,
               "modeled_net_at_best_bid": None, "net_break_even_observed": None}
        if bid is not None:
            if trade["asset_class"] != "ACCIONES" or number(trade.get("contract_cash_multiplier", 1)) != 1:
                raise ValueError("POST_STOP_FAMILY_MODEL_UNVERIFIED")
            fill = (number(bid, positive=True)*Decimal(".9998")).quantize(Decimal(".0001"))
            costs = expected_round_trip_cost(trade["entry_price"], fill, trade["quantity"], 1, fees,
                                             intraday_eligible=True)
            net = (fill-number(trade["entry_price"]))*number(trade["quantity"])-costs["explicit_fees"]
            row.update(modeled_net_at_best_bid=net, net_break_even_observed=net >= 0)
        stops.append(row)
    return {"mode": "READ_ONLY_OFFLINE", "real_order_routes": [],
            "factual_closed_count": factual["closed_count"], "source_sha256": hashes,
            "strict_target_replays_ars": targets, "grid_replays_ars": grid, "post_stop_120m": stops,
            "economic_edge_validated": False,
            "limitations": ["same factual entries; no portfolio capital/risk feedback",
                            "first touch/depth comes from extracted artifacts; full market paths are absent",
                            "grid horizon 17:00 ART changes more than the stop",
                            "best post-stop BID is observed sensitivity, not executable depth or overnight authority",
                            "historical diagnostic data is not out-of-sample validation"]}
