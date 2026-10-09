"""Independent offline reproduction of U09/U10/U22/U23 on a selected tree.

Run with --repository-root and --source-sha; emits a JSON receipt to stdout.
The probe retains the original #469 economic inputs and uses actual callers.
It cannot place orders or acquire a live/provider dataset.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--working-tree-dirty", action="store_true")
    args = parser.parse_args()
    root = args.repository_root.resolve()
    sys.path.insert(0, str(root))
    network_attempts = []

    def blocked_network(*_args, **_kwargs):
        network_attempts.append("NETWORK_ATTEMPT_BLOCKED")
        raise RuntimeError("OFFLINE_FINANCE_PROBE_NETWORK_FORBIDDEN")

    socket.socket.connect = blocked_network
    socket.create_connection = blocked_network
    os.environ["PAPER_SECTOR_CONCENTRATION_POLICY"] = "OBSERVATION_ONLY"
    os.environ.pop("POROTA_RUNTIME_SCHEMA_READY", None)
    from be_paper_engine import PaperBroker, PaperStore, Quote
    from bs_instrument_contracts import InstrumentContract
    from dh_paper_dynamic_risk_gate_hf6 import portfolio_capacity
    from rc6_paper_family_lifecycle import (FamilyPaperExecutor, apply_paper_event,
        future_cash_effect, future_positions, future_risk_snapshot)
    from rc6_ppi_future_contract_policy import standard_dlr_terms

    cutoff = "2026-10-05T13:00:00.000000-03:00"
    opening = "2026-10-05T12:59:00-03:00"

    def contract():
        terms = standard_dlr_terms("DLR/OCT26")
        return InstrumentContract("DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
            D("1000"), D("1"), "PPI_PRIMARY+A3_OFFICIAL:SYNTHETIC_469",
            expires_at=terms["expires_at"], minimum_quantity=D("1"),
            paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE", paper_margin_rate=D("1"),
            underlying=terms["underlying"])

    def broker(store, clock):
        return PaperBroker(store, initial_cash="100000000", risk_pct="0.05",
            max_positions=10, participation="0.1", max_position_pct="1",
            max_total_exposure_pct="1", clock_fn=lambda: clock[0], session_policy=None,
            require_supervisor=False, ai_mode="OFF", economics_mode="SHADOW", daily_loss_pct="5")

    files = ["bs_instrument_contracts.py", "rc6_paper_family_lifecycle.py",
             "rc6_ppi_future_contract_policy.py", "dh_paper_dynamic_risk_gate_hf6.py",
             "be_paper_engine.py", "bw_daily_risk.py", "rc6_performance/costs.py"]
    configuration = {"mode": "PRODUCTION_PAPER", "execution": "SIMULATED",
                     "ai_mode": "OFF", "economics_mode": "SHADOW",
                     "initial_cash": "100000000", "risk_pct": "0.05",
                     "max_positions": 10, "participation": "0.1",
                     "max_position_pct": "1", "max_total_exposure_pct": "1",
                     "daily_loss_pct": "5", "session_policy": None,
                     "PAPER_SECTOR_CONCENTRATION_POLICY": "OBSERVATION_ONLY"}
    result = {"source_sha": args.source_sha, "source_tree": args.source_tree,
              "source_state": "WORKTREE_NOT_FROZEN" if args.working_tree_dirty else "COMMITTED_SNAPSHOT",
              "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "configuration": configuration,
              "configuration_sha256": hashlib.sha256(json.dumps(
                  configuration, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
              "source_files_sha256": {
                  name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files},
              "schema_version": 1, "seed": "SYNTHETIC_469_FINANCE_V1",
              "cutoff": cutoff, "clock": "EXPLICIT_AWARE_ART_AND_UTC_MICROSECONDS",
              "environment": {"python": platform.python_version(), "sqlite": __import__("sqlite3").sqlite_version},
              "scope": "OFFLINE_SYNTHETIC_PAPER_ONLY", "external_account_terms": "NO_VERIFICADO",
              "external_20_sessions_master": "EXTERNAL_NO_VERIFICADO",
              "economic_edge": "EDGE_NO_DEMOSTRADO", "real_orders_sent": 0,
              "real_routes_used": []}
    with tempfile.TemporaryDirectory(prefix="rc6-finance-probe-") as scratch:
        directory = Path(scratch)
        clock = ["2026-10-05T10:30:00-03:00"]
        store = PaperStore(str(directory / "tick.sqlite"))
        engine = broker(store, clock)
        entry_quote = Quote("DLR/OCT26", "FUTUROS", "INMEDIATA", D("1578.5"),
            D("1578"), D("1579"), D("10"), D("10"), clock[0], contract=contract(),
            currency="ARS", market="A3", metadata_source="PPI_CATALOG:SYNTHETIC_469",
            book_at=clock[0], trade_at=clock[0], last_kind="TRADE")
        opened, reason, _ = engine._open_future(entry_quote, D("0.8"), {})
        if not opened:
            raise AssertionError("Positive-control DLR OPEN failed: " + reason)
        position = future_positions(store, active_only=True)[0]
        entry = D(position["entry_price"])
        expected_entry = ((entry_quote.ask * (1 + engine.slippage)) / D("0.5")).to_integral_value(
            rounding=ROUND_CEILING) * D("0.5")
        clock[0] = "2026-10-05T10:31:00-03:00"
        exit_quote = Quote("DLR/OCT26", "FUTUROS", "INMEDIATA", D("1580"),
            D("1580"), D("1580.5"), D("10"), D("10"), clock[0], contract=contract(),
            currency="ARS", market="A3", metadata_source="PPI_CATALOG:SYNTHETIC_469",
            book_at=clock[0], trade_at=clock[0], last_kind="TRADE")
        if not engine._close_future(exit_quote, position, "STOP_PAPER"):
            raise AssertionError("Positive-control DLR CLOSE failed")
        exit_price = D(future_positions(store)[0]["last_mark_price"])
        expected_exit = ((exit_quote.bid * (1 - engine.slippage)) / D("0.5")).to_integral_value(
            rounding=ROUND_FLOOR) * D("0.5")
        result["U09"] = {"entry": str(entry), "exit": str(exit_price),
                         "expected_entry": str(expected_entry), "expected_exit": str(expected_exit),
                         "round_trip_gross_error_ars_per_contract": str(
                             (expected_entry - entry + exit_price - expected_exit) * D("1000")),
                         "passed": entry == expected_entry and exit_price == expected_exit}

        temporal = []
        for action in ("MARK", "SETTLEMENT", "CLOSE"):
            for delta in (-500, -499, -100, -1, 0, 1, 100, 499, 500):
                db = PaperStore(str(directory / f"time-{action}-{delta}.sqlite"))
                executor = FamilyPaperExecutor(db)
                executor.open_future(contract(), lifecycle_id="FUT-1", event_id="OPEN",
                    entry_price="1500", entry_cost="100", quantity="1", occurred_at=opening, book_at=opening)
                before = future_risk_snapshot(db, "ARS", cutoff)
                at = (datetime.fromisoformat(cutoff) + timedelta(microseconds=delta)).isoformat()
                if action == "CLOSE":
                    executor.close_future(contract(), lifecycle_id="FUT-1", event_id="CLOSE",
                        exit_price="1520", exit_cost="100", book_at=at, occurred_at=at)
                else:
                    executor.mark_future(contract(), lifecycle_id="FUT-1", event_id=action,
                        mark_price="1510", book_at=at, occurred_at=at, settlement=action == "SETTLEMENT")
                after = future_risk_snapshot(PaperStore(db.path), "ARS", cutoff)
                expected_total = D("19800") if action == "CLOSE" else D("9900")
                passed = (after == before if delta > 0 else
                          after["realized"] + after["unrealized"] == expected_total)
                temporal.append({"event": action, "delta_us": delta,
                                 "cash_before": str(before["cash_effect"]),
                                 "cash_after": str(after["cash_effect"]),
                                 "active_before": before["active_count"],
                                 "active_after": after["active_count"], "passed": passed})
        result["U10"] = {"variants": temporal, "passed": all(case["passed"] for case in temporal)}

        store = PaperStore(str(directory / "capacity.sqlite"))
        engine = broker(store, [cutoff])
        engine.family_paper.open_future(contract(), lifecycle_id="FUT-1", event_id="OPEN",
            entry_price="1500", entry_cost="100", quantity="1", occurred_at=opening, book_at=opening)
        before = portfolio_capacity(engine, "ARS", cutoff)
        after_at = "2026-10-05T13:01:00-03:00"
        engine.family_paper.close_future(contract(), lifecycle_id="FUT-1", event_id="CLOSE",
            exit_price="1520", exit_cost="100", book_at=after_at, occurred_at=after_at)
        after = portfolio_capacity(engine, "ARS", cutoff)
        result["U22"] = {"open_stop_risk_before": str(before.open_stop_risk),
                         "open_stop_risk_after_later_close_same_cut": str(after.open_stop_risk),
                         "passed": before == after}

        store = PaperStore(str(directory / "idempotency.sqlite"))
        values = dict(lifecycle_id="FCI-1", event_id="REQ-1", family="FCI", instrument="FCI",
                      currency="ARS", to_state="SUBSCRIBE_REQUESTED", amount="-1000", occurred_at=cutoff)
        apply_paper_event(store, **values)
        equivalent = apply_paper_event(store, **values)
        conflict = None
        try:
            apply_paper_event(store, **(values | {"amount": "-2000", "occurred_at": after_at}))
        except ValueError as error:
            conflict = str(error)
        with store.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0]
            amount = connection.execute("SELECT ledger_total FROM paper_family_lifecycle").fetchone()[0]
        result["U23"] = {"equivalent_retry_idempotent": equivalent["idempotent"],
                         "conflicting_retry_error": conflict, "durable_count": count,
                         "durable_amount": amount,
                         "passed": bool(conflict) and equivalent["idempotent"] and count == 1 and D(amount) == -1000}
    result["network_attempts"] = len(network_attempts)
    result["passed"] = not network_attempts and all(result[key]["passed"] for key in ("U09", "U10", "U22", "U23"))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
