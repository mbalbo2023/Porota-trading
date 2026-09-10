"""P0 regression gate for audited RC6 PAPER take-profit behavior.

These tests are isolated, deterministic and network-free. They preserve the
sector-concentration BINDING fail-closed policy by providing explicit reviewed
sector evidence for the known GGAL fixture; the gate is never relaxed.
"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from be_paper_engine import D, PaperBroker, PaperStore
from bm_exit_supervisor import PositionExitSupervisor
from bq_exit_policy import PaperSessionPolicy
from test_production_paper_v1634 import quote


def key(position):
    return tuple(
        position[k]
        for k in ("symbol", "asset_class", "settlement", "currency", "market")
    )


@pytest.fixture
def opened_position(tmp_path, monkeypatch):
    # W10 is intentionally BINDING. Give this unrelated exit test a reviewed,
    # sourced sector identity rather than weakening/faking the policy mode.
    sector_map = tmp_path / "sector-map.csv"
    sector_map.write_text(
        "ticker,family,market,currency,settlement,sector,source,author,effective_at,reviewed\n"
        "GGAL,ACCIONES,BYMA,ARS,A-24HS,FINANCIERO,TEST_REVIEWED_FIXTURE,RC6_AUDIT,2026-09-10,true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POROTA_SECTOR_MAP_PATH", str(sector_map))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "BINDING")

    store = PaperStore(str(tmp_path / "take-profit.db"))
    broker = PaperBroker(store)
    opening = quote(at="2026-08-28T11:00:00-03:00")
    opened, reason, _ = broker._open(opening, D("0.8"), {})
    assert opened, reason
    position = store.open_positions()[0]
    assert D(position["target_price"]) > D(position["entry_price"])
    return broker, position


def target_quote(position, *, at, bid_size="1000", gap="0.20"):
    # quote() builds bid as price - 0.10, so gap=0.20 leaves the observed bid
    # 0.10 above the persisted target without fabricating a target fill.
    price = D(position["target_price"]) + D(gap)
    return quote(price=str(price), bid_size=bid_size, at=at)


def test_take_profit_closes_intraday_from_bid(opened_position):
    broker, position = opened_position
    at = "2026-08-28T11:05:00-03:00"
    q = target_quote(position, at=at)
    assert D(q.bid) >= D(position["target_price"])

    verdict = PositionExitSupervisor(broker, clock_fn=lambda: at).tick({key(position): q})[0]

    assert (verdict.state, verdict.cause) == ("CLOSED", "TAKE_PROFIT_PAPER")
    closed = broker.store.recent_closed()[0]
    assert closed["close_reason"] == "TAKE_PROFIT_PAPER"
    assert not broker.store.open_positions()


def test_take_profit_without_depth_stays_pending_and_retries(opened_position):
    broker, position = opened_position
    clock = ["2026-08-28T11:05:00-03:00"]
    supervisor = PositionExitSupervisor(broker, clock_fn=lambda: clock[0])

    no_depth = target_quote(position, at=clock[0], bid_size="0")
    first = supervisor.tick({key(position): no_depth})[0]
    assert (first.state, first.cause) == (
        "EXIT_PENDING_NO_LIQUIDITY",
        "TAKE_PROFIT_PAPER",
    )
    assert broker.store.open_positions()

    clock[0] = "2026-08-28T11:06:00-03:00"
    recovered = target_quote(position, at=clock[0], bid_size="1000")
    second = supervisor.tick({key(position): recovered})[0]
    assert (second.state, second.cause) == ("CLOSED", "TAKE_PROFIT_PAPER")
    assert broker.store.recent_closed()[0]["close_reason"] == "TAKE_PROFIT_PAPER"
    with broker.store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'"
        ).fetchone()[0] == 1


def test_take_profit_gap_uses_observed_bid_not_synthetic_target(opened_position):
    broker, position = opened_position
    at = "2026-08-28T11:05:00-03:00"
    q = target_quote(position, at=at, gap="10.00")
    target = D(position["target_price"])

    verdict = PositionExitSupervisor(broker, clock_fn=lambda: at).tick({key(position): q})[0]
    assert (verdict.state, verdict.cause) == ("CLOSED", "TAKE_PROFIT_PAPER")

    closed = broker.store.recent_closed()[0]
    exit_price = D(closed["exit_price"])
    assert exit_price != target
    assert target < exit_price <= D(q.bid)


def test_target_during_eod_window_has_explicit_eod_precedence(opened_position):
    broker, position = opened_position
    at = "2026-08-28T16:55:00-03:00"
    policy = PaperSessionPolicy()
    q = target_quote(position, at=at)
    assert D(q.bid) >= D(position["target_price"])

    verdict = PositionExitSupervisor(
        broker,
        clock_fn=lambda: at,
        session_policy=policy,
    ).tick({key(position): q})[0]

    # Current RC6 policy is flat-overnight: once EOD is due, that persisted
    # cause wins even if the same book is also above target. Keep this
    # deterministic until trigger attribution is deliberately redesigned.
    assert (verdict.state, verdict.cause) == ("CLOSED", "EOD_PAPER")
    assert broker.store.recent_closed()[0]["close_reason"] == "EOD_PAPER"
