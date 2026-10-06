#!/usr/bin/env python3
"""Exercise the real PAPER scalping promotion against binding economics.

All market data and storage are synthetic and temporary.  The only substituted
boundary is the runtime broker constructor: it returns a real PaperBroker with
the same requested scalping parameters and BINDING economics so the probe does
not import the network-facing runtime module.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import be_paper_engine as engine
import cf_intraday_scalping as scalping
from be_paper_engine import PaperBroker, PaperStore, Quote
from bm_exit_supervisor import PositionExitSupervisor
from bq_exit_policy import PaperSessionPolicy


D = Decimal


def payload(start: datetime, count: int) -> list[dict]:
    return [
        {
            "date": (start + timedelta(minutes=index)).isoformat(),
            "price": str(D("100") + D(index) / D("4")),
            "volume": str(20 if index % 2 == 0 else 10),
        }
        for index in range(count)
    ]


def main() -> None:
    old_environment = os.environ.copy()
    old_now = engine.now_iso
    old_runtime = sys.modules.get("bv_paper_runtime")
    result: dict[str, object] = {
        "scope": "SYNTHETIC_ONLY_PAPER_SHADOW",
        "real_orders_sent": 0,
        "real_routes_used": [],
        "network_calls": 0,
    }
    try:
        os.environ.update(
            {
                "PAPER_SCALPING_MODE": "ACTIVE_PAPER",
                "PAPER_ECONOMIC_GATE_MODE": "BINDING",
                "PAPER_SECTOR_CONCENTRATION_POLICY": "OBSERVATION_ONLY",
                "POROTA_DYNAMIC_CAPACITY_MODE": "OFF",
                "PAPER_INITIAL_CAPITAL_ARS": "1000000",
                "PAPER_SCALPING_MAX_OPEN_POSITIONS": "1",
                "PAPER_SCALPING_RISK_PER_TRADE": "0.001",
                "PAPER_SCALPING_STOP_LOSS_PCT": "0.008",
                "PAPER_SCALPING_TARGET_GAIN_PCT": "0.02",
            }
        )
        at = datetime.fromisoformat("2026-10-05T10:46:00-03:00")
        clock = [at]
        engine.now_iso = lambda: clock[0].isoformat()
        with tempfile.TemporaryDirectory(prefix="issue469-scalping-") as directory:
            store = PaperStore(str(Path(directory) / "paper.sqlite"))
            scalping.init_schema(store)
            record = {
                "ticker": "GGAL",
                "instrument_type": "ACCIONES",
                "market": "BYMA",
                "currency": "ARS",
                "settlement": "A-24HS",
                "capability": "READY_PAPER_SPOT",
                "status": "AVAILABLE",
            }
            start = at.replace(hour=10, minute=30, second=0, microsecond=0)
            first = scalping.persist_payload(
                store,
                record,
                scalping.normalize_payload(
                    payload(start, 15), received_at=(at - timedelta(minutes=1)).isoformat()
                ),
                received_at=(at - timedelta(minutes=1)).isoformat(),
            )
            second = scalping.persist_payload(
                store,
                record,
                scalping.normalize_payload(payload(start, 16), received_at=at.isoformat()),
                received_at=at.isoformat(),
            )
            quote = Quote(
                "GGAL",
                "ACCIONES",
                "A-24HS",
                D("103.75"),
                D("103.75"),
                D("103.80"),
                D("1000"),
                D("1000"),
                at.isoformat(),
                currency="ARS",
                market="BYMA",
                metadata_source="PPI_PRIMARY_SYNTHETIC_469",
                book_at=at.isoformat(),
                trade_at=at.isoformat(),
                last_kind="TRADE",
            )
            store.add_quote(quote)
            broker = PaperBroker(
                store,
                initial_cash="1000000",
                risk_pct="0.001",
                max_positions=5,
                participation="0.1",
                max_position_pct="0.25",
                max_total_exposure_pct="0.60",
                clock_fn=lambda: clock[0].isoformat(),
                session_policy=PaperSessionPolicy(),
                require_supervisor=True,
                ai_mode="OFF",
                economics_mode="BINDING",
                quote_max_age_seconds=120,
                trade_max_age_seconds=900,
                daily_loss_pct="2.5",
                stop_loss_pct="0.008",
                target_gain_pct="0.02",
                min_net_reward_risk="1.20",
            )
            supervisor = PositionExitSupervisor(
                broker,
                clock_fn=lambda: clock[0].isoformat(),
                session_policy=broker.session_policy,
                max_hold_minutes=360,
            )
            supervisor.tick()
            with store.connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO paper_exit_reader_state VALUES(1,?,'READY','synthetic')",
                    (at.isoformat(),),
                )

            candidate_action = scalping.evaluate_candidate(store, record, at=at.isoformat())
            with store.connect() as connection:
                candidate = dict(
                    connection.execute(
                        "SELECT * FROM scalping_candidates ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                )
            candidate_economics = json.loads(candidate["economics_json"])
            canonical_economics = broker._economic_diagnostics(quote)

            captured_overrides: dict[str, object] = {}
            runtime_stub = types.ModuleType("bv_paper_runtime")

            def broker_from_environment(_store, **overrides):
                captured_overrides.update(overrides)
                return broker

            runtime_stub.broker_from_environment = broker_from_environment
            sys.modules["bv_paper_runtime"] = runtime_stub
            promotion = scalping.promote_paper_candidate(store, record, at=at.isoformat())

            with store.connect() as connection:
                position = dict(
                    connection.execute(
                        """SELECT paper_id,status,quantity,entry_price,entry_cost,
                        stop_price,target_price,features_json FROM paper_positions"""
                    ).fetchone()
                )
                gate = dict(
                    connection.execute(
                        """SELECT technical_gate,ai_gate,patrimonial_gate,final_result,
                        reason,detail_json FROM trade_gate_evaluations ORDER BY id DESC LIMIT 1"""
                    ).fetchone()
                )
                state = dict(
                    connection.execute(
                        "SELECT mode,real_orders_sent FROM observer_state WHERE id=1"
                    ).fetchone()
                )
            persisted_features = json.loads(position["features_json"])
            persisted_gate_detail = json.loads(gate["detail_json"])

            clock[0] = at + timedelta(minutes=30)
            max_hold_quote = Quote(
                "GGAL",
                "ACCIONES",
                "A-24HS",
                D("103.75"),
                D("103.75"),
                D("103.80"),
                D("1000"),
                D("1000"),
                clock[0].isoformat(),
                currency="ARS",
                market="BYMA",
                metadata_source="PPI_PRIMARY_SYNTHETIC_469",
                book_at=clock[0].isoformat(),
                trade_at=clock[0].isoformat(),
                last_kind="TRADE",
            )
            store.add_quote(max_hold_quote)
            max_hold_verdicts = supervisor.tick(
                {("GGAL", "ACCIONES", "A-24HS", "ARS", "BYMA"): max_hold_quote}
            )
            with store.connect() as connection:
                closed = dict(
                    connection.execute(
                        """SELECT status,closed_at,exit_price,exit_cost,gross_pnl,
                        net_pnl,close_reason FROM paper_positions"""
                    ).fetchone()
                )

            reward = D(canonical_economics["net_reward_per_unit"])
            loss = D(canonical_economics["net_loss_per_unit"])
            quantity = D(position["quantity"])
            result.update(
                {
                    "intraday_contract": {"first": first, "second": second},
                    "candidate": {
                        "action": candidate_action,
                        "score": candidate["score"],
                        "reason": candidate["reason"],
                        "economics": candidate_economics,
                    },
                    "canonical_binding_economics": canonical_economics,
                    "promotion": {
                        "result": promotion,
                        "constructor_overrides": captured_overrides,
                        "position": {key: value for key, value in position.items() if key != "features_json"},
                        "persisted_economics": persisted_features["economics"],
                        "persisted_exit_policy": persisted_features["exit_policy"],
                        "scalping_max_hold_minutes": persisted_features["scalping_max_hold_minutes"],
                        "gate": {key: value for key, value in gate.items() if key != "detail_json"},
                        "gate_persisted_economics": persisted_gate_detail["economics"],
                        "observer_state": state,
                    },
                    "max_hold_control": {
                        "at": clock[0].isoformat(),
                        "verdicts": [verdict.__dict__ for verdict in max_hold_verdicts],
                        "closed_position": closed,
                    },
                    "counterfactual_math": {
                        "net_reward_per_unit": str(reward),
                        "net_loss_per_unit": str(loss),
                        "net_reward_risk": str(reward / loss),
                        "breakeven_win_rate": str(loss / (loss + reward)),
                        "equal_probability_ev_per_unit": str((reward - loss) / D("2")),
                        "equal_probability_ev_for_open_quantity": str((reward - loss) * quantity / D("2")),
                        "no_trade_nominal_payoff": "0",
                        "open_quantity": str(quantity),
                        "target_net_reward_for_open_quantity": str(reward * quantity),
                        "stop_net_loss_for_open_quantity": str(loss * quantity),
                    },
                    "binding_bypass_observed": (
                        candidate_action == "BUY_CANDIDATE"
                        and canonical_economics["passed"] is False
                        and promotion == "OPENED_SIMULATED"
                        and persisted_features["economics"]["passed"] is True
                    ),
                }
            )
    finally:
        engine.now_iso = old_now
        os.environ.clear()
        os.environ.update(old_environment)
        if old_runtime is None:
            sys.modules.pop("bv_paper_runtime", None)
        else:
            sys.modules["bv_paper_runtime"] = old_runtime
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
