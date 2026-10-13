"""Native paper stop/gap/poll/carry contracts; market execution stays unknown."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json

import pytest

from be_paper_engine import PaperBroker, PaperStore
from bm_exit_supervisor import PositionExitSupervisor
from bq_exit_policy import PaperSessionPolicy
from tests.test_production_paper_v1634 import quote


def identity(position):
    return tuple(position[key] for key in ("symbol", "asset_class", "settlement", "currency", "market"))


@pytest.fixture
def opened(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    clock = ["2026-08-28T11:00:00-03:00"]
    broker = PaperBroker(PaperStore(str(tmp_path / "gap-paper.db")), clock_fn=lambda: clock[0])
    initial = quote(at=clock[0]); assert broker._open(initial, Decimal("0.8"), {})[0]
    return broker, broker.store.open_positions()[0], clock


def sales(store):
    with store.connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM paper_fills WHERE side='SELL_SIMULATED'")]


def test_extreme_native_spread_blocks_scalping_candidate_without_claiming_economic_edge(tmp_path, monkeypatch):
    import bu_instrument_catalog as catalog
    import cf_intraday_scalping as scalping
    from tests.test_issue465_capability_cache import DAY, instrument, payload
    store = PaperStore(str(tmp_path / "spread-paper.db")); catalog.init_schema(store); scalping.init_schema(store)
    record = instrument(); now = DAY + timedelta(minutes=1)
    for at in (DAY, now):
        points = scalping.normalize_payload(payload(at), received_at=at.isoformat())
        scalping.persist_payload(store, record, points, received_at=at.isoformat())
    monkeypatch.setenv("PAPER_SCALPING_MAX_SPREAD", "0.005")
    q = replace(quote(price="108", at=now.isoformat()), bid=Decimal("100"), ask=Decimal("110"))
    store.add_quote(q)
    assert scalping.evaluate_candidate(store, record, at=now.isoformat()) == "HOLD"
    with store.connect() as db:
        candidate = dict(db.execute("SELECT * FROM scalping_candidates ORDER BY id DESC LIMIT 1").fetchone())
        assert candidate["reason"] == "SPREAD_TOO_WIDE"
        assert json.loads(candidate["economics_json"])["passed"] is False
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    # Observed intraday range is not OOS edge or a live fill probability.


def test_gap_through_stop_fills_current_bid_with_adverse_slippage_not_nominal_stop(opened):
    broker, position, clock = opened; clock[0] = "2026-08-28T11:01:00-03:00"
    gap = quote(price="85", at=clock[0])
    result = PositionExitSupervisor(broker, clock_fn=lambda: clock[0]).tick({identity(position): gap})[0]
    assert (result.state, result.cause) == ("CLOSED", "STOP_PAPER")
    fills = sales(broker.store); assert len(fills) == 1
    expected = (gap.bid * (1 - broker.slippage)).quantize(Decimal("0.0001"))
    assert Decimal(fills[0]["price"]) == expected < Decimal(position["stop_price"])
    closed = broker.store.recent_closed()[0]
    assert Decimal(closed["net_pnl"]) < 0 and closed["close_reason"] == "STOP_PAPER"


def test_gap_without_depth_persists_intent_then_restart_uses_new_factual_book(opened):
    broker, position, clock = opened; clock[0] = "2026-08-28T11:01:00-03:00"
    gap = quote(price="85", bid_size="0", at=clock[0])
    verdict = PositionExitSupervisor(broker, clock_fn=lambda: clock[0]).tick({identity(position): gap})[0]
    assert (verdict.state, verdict.cause) == ("EXIT_PENDING_NO_LIQUIDITY", "STOP_PAPER")
    assert not sales(broker.store)
    clock[0] = "2026-08-28T11:02:00-03:00"
    recovered = quote(price="105", at=clock[0])
    restarted = PaperBroker(PaperStore(broker.store.path), clock_fn=lambda: clock[0])
    supervisor = PositionExitSupervisor(restarted, clock_fn=lambda: clock[0])
    verdict = supervisor.tick({identity(position): recovered})[0]
    assert (verdict.state, verdict.cause) == ("CLOSED", "STOP_PAPER")
    assert supervisor.tick({identity(position): recovered}) == []
    fills = sales(restarted.store); assert len(fills) == 1
    expected = (recovered.bid * (1 - restarted.slippage)).quantize(Decimal("0.0001"))
    assert Decimal(fills[0]["price"]) == expected


def test_flash_between_supervisor_polls_does_not_invent_an_intrabar_stop_fill(opened):
    broker, position, clock = opened
    broker.store.add_quote(quote(price="85", at="2026-08-28T11:00:01-03:00"))
    recovered = quote(price="100", at="2026-08-28T11:00:02-03:00"); broker.store.add_quote(recovered)
    clock[0] = "2026-08-28T11:00:03-03:00"
    result = PositionExitSupervisor(broker, clock_fn=lambda: clock[0]).tick()[0]
    assert result.state == "OPEN" and result.cause is None
    assert len(broker.store.open_positions()) == 1 and not sales(broker.store)
    # The engine sees the latest sampled book. This test cannot establish
    # intrapoll fills, queue position, transport latency, or real stop execution.


def test_eod_without_book_preserves_carry_after_close_and_restart_until_executable_session(opened):
    broker, position, clock = opened; broker.session_policy = PaperSessionPolicy()
    clock[0] = "2026-08-28T16:51:00-03:00"
    supervisor = PositionExitSupervisor(broker, clock_fn=lambda: clock[0], session_policy=broker.session_policy)
    pending = supervisor.tick({})[0]
    assert (pending.state, pending.cause) == ("EXIT_PENDING_NO_QUOTE", "EOD_PAPER")
    clock[0] = "2026-08-28T17:05:00-03:00"
    q = quote(price="90", at=clock[0])
    pending = supervisor.tick({identity(position): q})[0]
    assert pending.state == "EXIT_PENDING_MARKET_CLOSED" and not sales(broker.store)
    assert broker.store.open_positions()
    clock[0] = "2026-08-31T11:01:00-03:00"
    restarted = PaperBroker(PaperStore(broker.store.path), session_policy=PaperSessionPolicy(), clock_fn=lambda: clock[0])
    q = quote(price="88", at=clock[0])
    closed = PositionExitSupervisor(restarted, clock_fn=lambda: clock[0], session_policy=restarted.session_policy).tick({identity(position): q})[0]
    assert (closed.state, closed.cause) == ("CLOSED", "EOD_PAPER")
    assert len(sales(restarted.store)) == 1
