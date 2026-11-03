from decimal import Decimal
import sqlite3

from be_paper_engine import PaperStore
from bt_caucion_paper import CaucionOffer
from di_caucion_cash_sweep_runtime_hf6 import (
    CASH_SWEEP_ORDER_ROUTING_ALLOWED,
    ObligationSnapshot,
    _persist_runtime,
    run_worker,
    run_paper_sweep,
)


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        return self


class _Store:
    def connect(self):
        return _Conn()


class _Broker:
    participation = Decimal("0.10")

    def __init__(self):
        self.store = _Store()
        self.allocations = 0

    def _cash(self, at, currency, *, connection, for_execution):
        assert currency == "ARS"
        assert for_execution is True
        return Decimal("100000")

    def allocate_caucion(self, offers, policy, request_id, *, as_of):
        self.allocations += 1
        assert len(offers) == 1
        assert request_id == "paper-caucion-cash-sweep:2026-10-01:ARS:v1"
        return {"status": "PLACED_SIMULATED", "code": "SIMULATED_OK", "paper_id": "PAPER-TEST"}


def test_off_market_empty_hold_does_not_prevent_later_fresh_paper_candidate():
    assert CASH_SWEEP_ORDER_ROUTING_ALLOWED is False
    broker = _Broker()
    snapshot = ObligationSnapshot(
        observed_at="2026-10-01T16:45:00-03:00",
        source="TEST_COMPLETE_LEDGER",
        complete=True,
        obligations=(),
    )
    common = dict(
        obligation_snapshot=snapshot,
        currency="ARS",
        as_of="2026-10-01T16:45:00-03:00",
        sweep_start_at="2026-10-01T16:30:00-03:00",
        order_cutoff_at="2026-10-01T16:55:00-03:00",
        liquidity_deadline="2026-10-02T17:00:00-03:00",
        schedule_source="TEST_PAPER_SCHEDULE",
        request_id="paper-caucion-cash-sweep:2026-10-01:ARS:v1",
        participation=Decimal("0.10"),
        max_quote_age_seconds=30,
    )

    first = run_paper_sweep(broker, [], **common)
    assert first["status"] == "HOLD"
    assert first["code"] == "EXACT_FEE_OR_VERSIONED_PAPER_TARIFF_MISSING"
    assert broker.allocations == 0

    offer = CaucionOffer(
        instrument_id="CAUCION-TEST",
        currency="ARS",
        annual_rate_fraction=Decimal("0.50"),
        start_date="2026-10-01",
        maturity_at="2026-10-02T16:00:00-03:00",
        quoted_at="2026-10-01T16:44:45-03:00",
        available_principal=Decimal("1000000"),
        minimum_principal=Decimal("1000"),
        principal_step=Decimal("1000"),
        day_count_basis=365,
        fee_payment="MATURITY",
        metadata_source="CONTRACT_EVIDENCE_V2:test",
        quoted_total_fees=Decimal("10"),
        fee_quote_principal=Decimal("100000"),
    )

    second = run_paper_sweep(broker, [offer], **common)
    assert second["status"] == "PLACED_SIMULATED"
    assert second["code"] == "SIMULATED_OK"
    assert second["allocation"]["paper_id"] == "PAPER-TEST"
    assert broker.allocations == 1


def test_post_cutoff_does_not_republish_prior_paper_placement(tmp_path, monkeypatch):
    import bv_paper_runtime
    import di_caucion_cash_sweep_runtime_hf6 as sweep

    store = PaperStore(str(tmp_path / "paper.db"))
    placed_at = "2026-10-15T16:40:00-03:00"
    _persist_runtime(store, at=placed_at, state="PLACED_SIMULATED",
                     code="SIMULATED_OK", request_id="daily-test", paper_id="PAPER-1",
                     result={"status": "PLACED_SIMULATED", "code": "SIMULATED_OK"})

    class OneTick:
        stopped = False
        observed = None

        def is_set(self):
            return self.stopped

        def wait(self, _interval):
            with sqlite3.connect(store.path) as c:
                self.observed = c.execute("""SELECT state,code,last_attempt_at,request_id,paper_id
                  FROM paper_caucion_cash_sweep_state WHERE id=1""").fetchone()
            self.stopped = True

    stop = OneTick()
    monkeypatch.setattr(bv_paper_runtime, "broker_from_environment", lambda *_args, **_kwargs: _Broker())
    monkeypatch.setattr(sweep, "offers_from_store", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("post-cutoff offers read")))
    monkeypatch.setattr(sweep, "_existing_daily_allocation", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("post-cutoff allocation replay")))
    run_worker(store, stop, clock_fn=lambda: "2026-10-15T17:05:00-03:00")
    assert stop.observed == ("WAITING_WINDOW", "SWEEP_WINDOW_CLOSED", placed_at,
                             "daily-test", "PAPER-1")
    with sqlite3.connect(store.path) as c:
        assert c.execute("SELECT COUNT(*) FROM paper_caucion_cash_sweep_attempts").fetchone()[0] == 1
