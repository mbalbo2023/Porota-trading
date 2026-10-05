"""Offline fixtures written by the canonical PAPER and SHADOW producers.

The instruments and prices are synthetic. Schema, decision capture, family
accounting, experiments, committed generations and readers are production code.
No client, provider route or production workspace participates.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gc
import os
from pathlib import Path
from unittest.mock import patch

from be_paper_engine import PaperBroker, PaperStore, Quote
from bf_production_paper_observer import _support_schema
from bq_exit_policy import PaperSessionPolicy
from cf_intraday_scalping import init_schema
from cg_paper_workspace import artifact_root
from rc6_dynamic_universe.common import identity
from rc6_paper_family_lifecycle import FamilyPaperExecutor
from rc6_shadow_runtime.persistence import read_committed_generation, shadow_evidence_root
from rc6_shadow_runtime.worker import ShadowRuntime
from tests.test_rc6_future_programming_complete import dlr

AS_OF = datetime(2026, 10, 5, 16, tzinfo=timezone.utc)


@dataclass
class NativeFixture:
    database: Path
    as_of: datetime
    root: Path
    cut: dict
    store: PaperStore
    worker: ShadowRuntime
    broker: PaperBroker
    clock: list


def native_fixture(tmp_path, *, as_of=AS_OF, count=25, with_future=True, with_spot=True):
    fixture = _build_native_fixture(tmp_path, as_of=as_of, count=count,
                                    with_future=with_future, with_spot=with_spot)
    # SQLite's transaction context commits but does not close a connection.
    # Native writer UDFs may retain cycles until GC; finalize those writers
    # before measuring read custody, so their last-close WAL checkpoint cannot
    # run as a side effect of allocations during the subsequent render.
    gc.collect()
    return fixture


def _build_native_fixture(tmp_path, *, as_of, count, with_future, with_spot):
    path = Path(tmp_path) / "native-paper.db"
    store = PaperStore(str(path))
    _support_schema(store)
    init_schema(store)
    start = as_of - timedelta(minutes=10)
    preopen = as_of.replace(hour=13, minute=20, second=0, microsecond=0)
    with store.connect() as connection:
        for index in range(count):
            asset = dict(ticker=f"T{index:03d}", instrument_type="ACCIONES", market="BYMA",
                         currency="ARS", settlement="A-24HS")
            connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (*identity(asset), "OFFLINE SYNTHETIC", "fixture", (preopen-timedelta(minutes=1)).isoformat(), "test",
                 "AVAILABLE", "READY_PAPER_SPOT", "{}"))
    clock = [start]
    def native_clock():
        clock[0] += timedelta(microseconds=1)
        return clock[0].isoformat(timespec="microseconds")
    broker = PaperBroker(store, initial_cash="10000000", initial_cash_by_currency={"USD": "1000"},
        risk_pct=".005", participation=".1", max_position_pct="1", max_total_exposure_pct="1",
        clock_fn=native_clock, session_policy=PaperSessionPolicy(), require_supervisor=False,
        ai_mode="OFF", economics_mode="SHADOW", signal_min_samples=8)
    # Resolve exactly the root the launcher/worker/dashboard share. No caller
    # constructs a .shadow fallback or publishes manually fabricated bundles.
    worker = ShadowRuntime.from_environment(path, source_roots=[])
    worker.tick(preopen)
    prices = ("100", "101", "100.5", "102", "101.5", "103", "103.5", "104")
    for index, price in enumerate(prices):
        at = start + timedelta(minutes=index + 1)
        quote = Quote("T000", "ACCIONES", "A-24HS", Decimal(price), Decimal(price),
            Decimal(price) + Decimal(".1"), Decimal(1000), Decimal(1000), at.isoformat(),
            currency="ARS", market="BYMA", metadata_source="PPI_NATIVE_OFFLINE_FIXTURE",
            book_at=at.isoformat(), trade_at=at.isoformat(), last_kind="TRADE")
        store.add_quote(quote)
        clock[0] = at
        if index == len(prices) - 1 and with_spot:
            # Synthetic tickers have no sector authority. The native sector
            # policy is explicitly observational for this offline fixture.
            with patch.dict(os.environ, {"PAPER_SECTOR_CONCENTRATION_POLICY": "OBSERVATION_ONLY"}):
                broker.on_quote(quote)
            assert store.open_positions(), "Native PAPER caller did not open the synthetic spot"
        worker.tick(at + timedelta(seconds=1))
    if with_future:
        executor = FamilyPaperExecutor(store)
        opened = as_of - timedelta(minutes=1)
        executor.open_future(dlr(), lifecycle_id="FUT-UI", event_id="FUT-UI:OPEN",
            entry_price="1500", quantity="1", entry_cost="100", occurred_at=opened.isoformat())
        mark = as_of - timedelta(seconds=1)
        executor.mark_future(dlr(), lifecycle_id="FUT-UI", event_id="FUT-UI:MARK",
            mark_price="1510", book_at=mark.isoformat(), occurred_at=mark.isoformat())
    store.state(process_state="RUNNING", session_state="MARKET_OPEN", ppi_auth="OK",
                heartbeat_at=as_of.isoformat(), last_market_data_at=as_of.isoformat(), real_orders_sent=0)
    clock[0] = as_of
    broker.mark_equity({"T000": quote}, as_of=as_of.isoformat())
    worker.tick(as_of)
    root = shadow_evidence_root(path)
    assert root == artifact_root(path) / "dynamic-shadow"
    cut = read_committed_generation(root)
    return NativeFixture(path, as_of, root, cut, store, worker, broker, clock)
