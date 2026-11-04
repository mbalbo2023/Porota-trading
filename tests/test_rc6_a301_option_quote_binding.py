"""A301: an OPTION fill must use the exact quote of its native BUY decision.

All quotes and the PAPER database below are isolated synthetic fixtures.  The
test cannot authenticate a PPI source or submit a broker order.
"""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from be_paper_engine import PaperBroker
from tests import test_rc6_a180_ten_family_strategy_integration as fixture_support


@pytest.fixture
def case(monkeypatch):
    old = fixture_support.FamilyIntegration()
    old.setUp()
    try:
        old.seed_underlying()
        broker = PaperBroker(
            old.store, initial_cash="1000000", risk_pct="0.01",
            max_position_pct="0.25", max_total_exposure_pct="1",
            daily_loss_pct="2.5", daily_soft_stop_pct="1.5",
            ai_mode="OFF", require_ai=False, clock_fn=fixture_support.stamp,
            admission_guard=lambda _q, _store: (True, ""),
        )
        q = fixture_support.option_quote()
        old.store.add_quote(q)
        action, score, reason, features = broker.decide(q)
        assert (action, reason) == ("BUY", "OPTION_LONG_PAPER_CANDIDATE")
        monkeypatch.setattr("cg_paper_workspace.database_path", lambda: Path(old.store.path))
        monkeypatch.setattr("rc6_paper_admission_gate.admission_status", lambda: (True, ""))
        yield broker, q, score, reason, features
    finally:
        old.doCleanups()


def _record(case):
    broker, q, score, reason, features = case
    strategy = features["strategy"]
    key = (f"{strategy}:{q.symbol}:{q.asset_class}:{q.settlement}:"
           f"{q.currency}:{q.market}:{q.observed_at[:16]}:BUY")
    features["native_decision_key"] = key
    assert broker.store.record_decision(key, q, "BUY", score, reason, features)
    features["economics"] = broker._economic_diagnostics(q)
    features["economics_mode"] = broker.economics_mode
    return key


@pytest.mark.parametrize("change", [
    lambda q: replace(q, ask=Decimal("1.5")),
    lambda q: replace(q, bid=Decimal("0.94")),
    lambda q: replace(q, ask_size=Decimal("21")),
    lambda q: replace(q, book_at="2026-10-29T11:29:59-03:00"),
    lambda q: replace(q, contract=replace(q.contract, strike=Decimal("101"))),
    lambda q: replace(q, metadata_source="PPI_CATALOG:REVISED"),
])
def test_native_buy_rejects_different_quote_inside_locked_admission(case, change):
    _record(case)
    broker, q, score, _, features = case
    opened, reason, paper_id = broker._open(change(q), score, features)
    assert (opened, reason, paper_id) == (
        False, "OPTION_NATIVE_QUOTE_CHANGED_BEFORE_ADMISSION", None)
    with broker.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0


def test_record_decision_rejects_caller_supplied_wrong_quote_digest(case):
    broker, q, score, reason, features = case
    features["native_quote_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="OPTION_NATIVE_QUOTE_DIGEST_MISMATCH"):
        broker.store.record_decision("synthetic-wrong-hash", q, "BUY", score, reason, features)
    with broker.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0] == 0


def test_canonical_option_alias_also_receives_quote_binding(case):
    broker, q, score, reason, features = case
    alias = replace(q, asset_class="OPCION")
    assert broker.store.record_decision("synthetic-option-alias", alias, "BUY", score,
                                        reason, features)
    assert len(features["native_quote_sha256"]) == 64


def test_normal_on_quote_binds_original_quote_and_retry_cannot_duplicate_fill(case):
    broker, q, _, _, _ = case
    broker.on_quote(q)
    with broker.store.connect() as db:
        row = db.execute("SELECT score,features_json FROM paper_decisions WHERE symbol=?",
                         (q.symbol,)).fetchone()
        assert row is not None
        import json
        features = json.loads(row["features_json"])
        assert len(features["native_quote_sha256"]) == 64
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    opened, reason, paper_id = broker._open(q, Decimal(row["score"]), features)
    assert (opened, paper_id) == (False, None)
    assert reason in {"Ya existe una posicion abierta del simbolo",
                      "PAPER_DECISION_KEY_ALREADY_COMMITTED"}
    with broker.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
        assert db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
