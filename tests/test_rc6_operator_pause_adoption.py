"""A stopped PAPER pair retains obligations; no synthetic settlement or ACK."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from be_paper_engine import PaperStore
from bt_caucion_paper import pending_proceeds
from scripts.rc6_operator_pause_receipt import (
    assert_pause_book_preserved, snapshot_paper_obligations)
from test_production_paper_v1634 import partial_spot


AT = "2026-08-25T14:03:00+00:00"


@pytest.fixture
def pending_t1(partial_spot):
    broker, position, _, sell = partial_spot("A-24HS", "ARS")
    assert broker._close(position, sell(), "TEST")
    assert broker._close(broker.store.open_positions()[0], sell(2, "100"), "TEST")
    return broker


def test_pending_t1_and_outbox_survive_restart_without_modifying_book(pending_t1):
    path = pending_t1.store.path
    with pending_t1.store.connect() as db:
        before_dump = list(db.iterdump())
    before = snapshot_paper_obligations(path, as_of=AT)
    assert before["open_counts"] == {
        "spot": 0, "caucion": 0, "future": 0, "fci": 0, "futures_lifecycle": 0}
    assert float(before["pending_proceeds_by_currency"]["ARS"]) > 0
    assert before["tables"]["paper_spot_sales"]["count"] == 2
    assert before["tables"]["paper_notification_outbox"]["count"] >= 2
    assert "POROTA" not in str(before) and "SELL_SIMULATED" not in str(before)
    PaperStore(path)  # Initialization/restart cannot settle T+1 or send an alert.
    after = snapshot_paper_obligations(path, as_of=AT)
    assert assert_pause_book_preserved(before, after)
    with pending_t1.store.connect() as db:
        assert list(db.iterdump()) == before_dump


def test_open_partial_cannot_be_adopted_even_with_receivables(partial_spot):
    broker, position, _, sell = partial_spot("A-24HS", "ARS")
    assert broker._close(position, sell(), "TEST")
    assert broker.store.open_positions()
    assert pending_proceeds(broker.store, AT) > 0
    with pytest.raises(ValueError, match="SPOT_EXIT_PENDING"):
        snapshot_paper_obligations(broker.store.path, as_of=AT)


def test_open_without_exit_intent_is_still_not_flat(partial_spot):
    broker, _, _, _ = partial_spot("A-24HS", "ARS")
    witness = snapshot_paper_obligations(broker.store.path, as_of=AT)
    assert witness["open_counts"]["spot"] == 1
    with pytest.raises(ValueError, match="OBLIGATIONS_CHANGED_OR_OPEN"):
        assert_pause_book_preserved(witness, witness)


@pytest.mark.parametrize("table,column,value", [
    ("paper_spot_sales", "basis", "UNKNOWN"),
    ("paper_notification_outbox", "state", "UNKNOWN"),
])
def test_broken_obligation_fails_closed(pending_t1, table, column, value):
    with pending_t1.store.connect() as db:
        db.execute(f"UPDATE {table} SET {column}=?", (value,))
    with pytest.raises(ValueError):
        snapshot_paper_obligations(pending_t1.store.path, as_of=AT)


def test_single_close_receivable_must_reconcile(tmp_path, monkeypatch):
    from dataclasses import replace
    from be_paper_engine import D, PaperBroker
    from test_production_paper_v1634 import quote
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    broker = PaperBroker(PaperStore(str(tmp_path / "single.db")), initial_cash="10000")
    initial = replace(quote(at="2026-08-25T14:00:00Z", ask_size="100"),
                      settlement="A-24HS")
    assert broker._open(initial, D(".8"), {})[0]
    position = broker.store.open_positions()[0]
    final = replace(initial, bid=D("110"), ask=D("111"),
                    observed_at="2026-08-25T14:01:00Z",
                    book_at="2026-08-25T14:01:00Z")
    assert broker._close(position, final, "TEST")
    good = snapshot_paper_obligations(broker.store.path, as_of=AT)
    assert good["tables"]["paper_sale_receivables"]["count"] == 1
    assert float(good["pending_proceeds_by_currency"]["ARS"]) > 0
    with broker.store.connect() as db:
        db.execute("UPDATE paper_sale_receivables SET net_proceeds='9999'")
    with pytest.raises(ValueError):
        snapshot_paper_obligations(broker.store.path, as_of=AT)


def test_after_stop_tampering_rejects_even_if_amounts_stay_equal(pending_t1):
    before = snapshot_paper_obligations(pending_t1.store.path, as_of=AT)
    with pending_t1.store.connect() as db:
        db.execute("UPDATE paper_notification_outbox SET body=body||' altered' WHERE id="
                   "(SELECT MIN(id) FROM paper_notification_outbox)")
    after = snapshot_paper_obligations(pending_t1.store.path, as_of=AT)
    assert after["pending_proceeds_by_currency"] == before["pending_proceeds_by_currency"]
    with pytest.raises(ValueError, match="OBLIGATIONS_CHANGED_OR_OPEN"):
        assert_pause_book_preserved(before, after)


def test_future_clock_and_missing_database_rejected(tmp_path):
    with pytest.raises(ValueError, match="FUTURE_AS_OF"):
        snapshot_paper_obligations(tmp_path / "missing.db", as_of="3000-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="DB_UNSAFE"):
        snapshot_paper_obligations(tmp_path / "missing.db", as_of=AT)
