import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from be_paper_engine import D, PaperBroker, PaperStore
from bl_candle_engine import fingerprint
from test_production_paper_v1634 import quote


AT = datetime(2026, 11, 26, 15, tzinfo=timezone.utc)
GENERATION = "a" * 64


def iol_quote(minute, price):
    trade_at = AT + timedelta(minutes=minute)
    trade = trade_at.isoformat()
    receipt = (trade_at + timedelta(seconds=2)).isoformat()
    return replace(
        quote(at=receipt, price=str(price)),
        metadata_source="IOL_REST",
        book_at=trade,
        trade_at=trade,
        iol_trade_evidence={
            "identity": ["GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"],
            "gateway_generation": GENERATION,
            "trade_at": trade,
            "received_at": (trade_at + timedelta(seconds=1)).isoformat(),
            "quote_received_at": receipt,
            "price": str(price),
        },
    )


@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "effective-runtime.db"))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    monkeypatch.setenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW")
    monkeypatch.delenv("POROTA_RC6_MARKET_PROVIDER", raising=False)
    return PaperBroker(PaperStore(str(tmp_path / "spot-fixture.db")), initial_cash="1000000", ai_mode="OFF",
                       signal_min_samples=6)


def test_iol_signal_warms_up_with_only_homogeneous_admitted_events(broker, monkeypatch):
    monkeypatch.setenv("POROTA_RC6_MARKET_PROVIDER", "IOL_REST")
    for minute in range(5):
        broker.store.add_quote(replace(
            iol_quote(minute, 100 + minute),
            metadata_source="PPI_CATALOG:ARCHIVE",
            opening_block_reason="PROVIDER_QUOTE_REJECTED",
            iol_trade_evidence=None,
        ))
    current = iol_quote(5, 105)
    broker.store.add_quote(current)

    action, _, reason, features = broker.decide(current)
    assert action == "HOLD" and "1/6" in reason
    inputs = features["entry_signal_inputs"]
    assert inputs["source"].startswith("IOL_NATIVE_DISTINCT_TRADES")
    assert [row["price"] for row in inputs["price_samples"]] == ["105"]

    for minute in range(5):
        broker.store.add_quote(iol_quote(minute, 100 + minute))
    action, _, _, features = broker.decide(current)
    assert action == "BUY"
    rows = features["entry_signal_inputs"]["price_samples"]
    assert [row["price"] for row in rows] == [str(value) for value in range(100, 106)]
    assert all(row["source"] == row["metadata_source"] == "IOL_REST" for row in rows)
    assert all(row["iol_trade_evidence"]["gateway_generation"] == GENERATION for row in rows)


@pytest.mark.parametrize("revision", [
    "hold", "non_trade", "other_provider", "missing_receipt", "wrong_identity", "wrong_generation",
    "wrong_receipt_clock", "wrong_price",
])
def test_revoked_iol_trade_cannot_supply_a_sample(broker, revision):
    for minute in range(6):
        broker.store.add_quote(iol_quote(minute, 100 + minute))
    current = iol_quote(5, 105)
    sample = iol_quote(2, 102)
    assert broker.decide(current)[0] == "BUY"
    proof = sample.iol_trade_evidence
    updates = {
        "hold": {"opening_block_reason": "PROVIDER_REVOKED"},
        "non_trade": {"last_kind": "UNKNOWN"},
        "other_provider": {"metadata_source": "PPI_CATALOG:ARCHIVE"},
        "missing_receipt": {"iol_trade_evidence": None},
        "wrong_identity": {"iol_trade_evidence": {**proof, "identity": [*proof["identity"][:-1], "INMEDIATA"]}},
        "wrong_generation": {"iol_trade_evidence": {**proof, "gateway_generation": "b" * 64}},
        "wrong_receipt_clock": {"iol_trade_evidence": {**proof, "received_at": proof["trade_at"],
                                                         "quote_received_at": AT.isoformat()}},
        "wrong_price": {"iol_trade_evidence": {**proof, "price": "999"}},
    }
    broker.store.add_quote(replace(sample, **updates[revision]))
    action, _, reason, features = broker.decide(current)
    assert action == "HOLD" and "5/6" in reason
    assert sample.trade_at not in {item["source_at"] for item in features["entry_signal_inputs"]["price_samples"]}


@pytest.mark.parametrize("revision", ["worse_book", "hold"])
def test_spot_fill_rechecks_exact_book_after_sizing(broker, monkeypatch, revision):
    import dh_paper_dynamic_risk_gate_hf6 as risk

    candidate = iol_quote(5, 100)
    store = broker.store
    store.add_quote(candidate)
    original_capacity = risk.portfolio_capacity
    changes = {
        "worse_book": {"ask": D("110"), "ask_size": D("1")},
        "hold": {"opening_block_reason": "PROVIDER_REVOKED", "last_kind": "UNKNOWN", "book_at": None, "trade_at": None},
    }
    revised = replace(candidate, **changes[revision])
    injected = False

    def write_after_preview(*args, **kwargs):
        nonlocal injected
        result = original_capacity(*args, **kwargs)
        if kwargs.get("connection") is None and kwargs.get("candidate_risk") and not injected:
            store.add_quote(revised)
            injected = True
        return result

    monkeypatch.setattr(risk, "portfolio_capacity", write_after_preview)
    assert broker._open(candidate, D("1"), {}) == (False, "SPOT_BOOK_CHANGED_BEFORE_FILL", None)
    assert injected
    with store.connect() as connection:
        for table in ("paper_positions", "paper_fills", "paper_book_consumption", "decision_evidence_snapshots"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_spot_fill_and_atomic_evidence_share_locked_book_digest(broker):
    store = broker.store
    candidate = iol_quote(5, 100)
    store.add_quote(candidate)
    store.add_quote(replace(candidate, settlement="INMEDIATA", ask_size=D("1")))
    assert broker._open(candidate, D("1"), {})[0]

    with store.connect() as connection:
        receipt = connection.execute("""SELECT f.side, p.features_json, b.instrument_key, b.book_at, b.book_json
            FROM paper_fills f JOIN paper_positions p USING(paper_id)
            JOIN paper_book_consumption b ON b.fill_id=f.id""").fetchone()
        evidence = json.loads(connection.execute(
            "SELECT payload_json FROM decision_evidence_snapshots").fetchone()[0])
        assert connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
    binding = json.loads(receipt["features_json"])["entry_book_binding"]
    assert receipt["side"] == "BUY_SIMULATED"
    assert binding == {
        "snapshot_id": 1,
        "book_sha256": fingerprint((receipt["instrument_key"], receipt["book_at"], json.loads(receipt["book_json"]))),
    }
    assert evidence["inputs_used"]["entry_book_binding"] == binding


def test_existing_history_preserves_exact_utc_cutoffs_and_uses_bounded_range(broker, monkeypatch):
    store = broker.store
    end = AT + timedelta(microseconds=456)
    start = end - timedelta(minutes=30)
    early = start - timedelta(microseconds=1)
    late = end + timedelta(microseconds=1)
    offset = timezone(timedelta(hours=-3, seconds=7))
    offset_start = start.astimezone(offset).isoformat()
    candidate = quote(at=end.isoformat(), price="101")
    with store.connect() as connection:
        connection.execute("DROP INDEX IF EXISTS idx_snapshot_trade_window")
    store.add_quote(quote(at=early.isoformat(), price="999"))
    store.add_quote(quote(at=offset_start, price="100"))
    store.add_quote(candidate)
    store.add_quote(quote(at=late.isoformat(), price="998"))
    store.add_quote(replace(candidate, observed_at=late.isoformat(), last=D("997"),
                            opening_block_reason="PROVIDER_REVOKED"))

    # Existing databases gain the index without rewriting old rows or clocks.
    store.init_db()
    connect = store.connect
    steps = []

    def measured_connect():
        connection = connect()
        connection.set_progress_handler(lambda: steps.append(1) or 0, 100)
        return connection

    monkeypatch.setattr(store, "connect", measured_connect)
    expected = [D("100"), D("101")]
    prices = store.signal_prices(candidate, end)
    assert prices == expected
    assert prices.observations[0]["source_at"] == offset_start
    short_scan = len(steps)
    archived_at = start - timedelta(days=5)
    archive_stamps = [archived_at.astimezone(tz).isoformat()
                      for tz in (timezone(timedelta(hours=-3)), offset)]
    # A raw SQLite writer does not need rc6_instant_us to maintain the new index.
    with sqlite3.connect(store.path) as connection:
        connection.executemany("""INSERT INTO market_snapshots
            (source,observed_at,symbol,asset_class,settlement,last,bid,ask,bid_size,ask_size,currency,market,
             book_at,trade_at,last_kind)
            VALUES('PRODUCTION_PAPER',?,'GGAL','ACCIONES','A-24HS','99','98','100','100','100',
                   'ARS','BYMA',?,?,'TRADE')""", ((stamp, stamp, stamp) for stamp in archive_stamps for _ in range(10_000)))
    steps.clear()
    assert store.signal_prices(candidate, end) == expected
    assert len(steps) <= short_scan + 3
    assert store.signal_prices(candidate, late) == [D("998")]
