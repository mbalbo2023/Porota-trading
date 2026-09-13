from datetime import datetime, timedelta
from decimal import Decimal

from be_paper_engine import PaperBroker, PaperStore
from di_caucion_cash_sweep_runtime_hf6 import CashObligation
from rc6_paper_obligation_snapshot import build_paper_only_obligation_snapshot

NOW = datetime.fromisoformat("2026-09-14T15:00:00-03:00")
DEADLINE = datetime.fromisoformat("2026-09-15T16:30:00-03:00")


def broker(tmp_path):
    return PaperBroker(
        PaperStore(str(tmp_path / "paper.db")),
        initial_cash="1000000",
        daily_loss_pct=None,
        clock_fn=lambda: NOW,
    )


def test_clean_paper_ledger_produces_complete_zero_extra_obligation_snapshot(tmp_path):
    b = broker(tmp_path)
    snap = build_paper_only_obligation_snapshot(
        b, as_of=NOW, currency="ARS", liquidity_deadline=DEADLINE
    )
    assert snap.complete is True
    assert snap.source == "PAPER_LEDGER_RECONCILED_V1"
    assert snap.obligations == ()


def test_real_order_counter_nonzero_fails_closed(tmp_path):
    b = broker(tmp_path)
    with b.store.connect() as c:
        c.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
    snap = build_paper_only_obligation_snapshot(
        b, as_of=NOW, currency="ARS", liquidity_deadline=DEADLINE
    )
    assert snap.complete is False
    assert "REAL_ORDERS_SENT_NONZERO" in snap.source


def test_non_paper_mode_fails_closed(tmp_path):
    b = broker(tmp_path)
    with b.store.connect() as c:
        c.execute("UPDATE observer_state SET mode='PRODUCTION' WHERE id=1")
    snap = build_paper_only_obligation_snapshot(
        b, as_of=NOW, currency="ARS", liquidity_deadline=DEADLINE
    )
    assert snap.complete is False
    assert "MODE_NOT_PRODUCTION_PAPER" in snap.source


def test_explicit_extra_obligations_are_filtered_by_currency_and_deadline(tmp_path):
    b = broker(tmp_path)
    ars_due = CashObligation("ARS", Decimal("1500"), (NOW + timedelta(hours=2)).isoformat(), "TEST_DUE", "VERIFIED_TEST")
    ars_late = CashObligation("ARS", Decimal("900"), (DEADLINE + timedelta(hours=1)).isoformat(), "TEST_LATE", "VERIFIED_TEST")
    usd_due = CashObligation("USD_MEP", Decimal("100"), (NOW + timedelta(hours=2)).isoformat(), "TEST_USD", "VERIFIED_TEST")
    snap = build_paper_only_obligation_snapshot(
        b,
        as_of=NOW,
        currency="ARS",
        liquidity_deadline=DEADLINE,
        additional_obligations=(ars_due, ars_late, usd_due),
    )
    assert snap.complete is True
    assert snap.obligations == (ars_due,)


def test_deadline_before_snapshot_fails_closed(tmp_path):
    b = broker(tmp_path)
    snap = build_paper_only_obligation_snapshot(
        b,
        as_of=NOW,
        currency="ARS",
        liquidity_deadline=NOW - timedelta(seconds=1),
    )
    assert snap.complete is False
    assert "LIQUIDITY_DEADLINE_BEFORE_SNAPSHOT" in snap.source


def test_invalid_extra_obligation_type_fails_closed(tmp_path):
    b = broker(tmp_path)
    snap = build_paper_only_obligation_snapshot(
        b,
        as_of=NOW,
        currency="ARS",
        liquidity_deadline=DEADLINE,
        additional_obligations=({"amount": 1},),
    )
    assert snap.complete is False
    assert "ADDITIONAL_OBLIGATION_INVALID" in snap.source
