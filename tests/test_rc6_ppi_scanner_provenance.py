"""A428: scanner PPI origin is distinct from PRODUCTION_PAPER execution mode."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from be_paper_engine import PaperStore, Quote, SOURCE
from rc6_ppi_scanner_provenance import (
    FILENAME, ORIGIN, SCHEMA, PRODUCER, ScannerPrimaryRecorder, primary_records,
    receipt_path,
)


def quote(symbol="GGAL", *, currency="ARS", market="BYMA", trade_at=None,
          book_at=None, observed_at=None, last_kind="TRADE"):
    now = datetime.now(timezone.utc) - timedelta(seconds=3)
    observed_at = observed_at or now.isoformat()
    trade_at = now.isoformat() if trade_at is None else trade_at
    book_at = now.isoformat() if book_at is None else book_at
    return Quote(symbol=symbol, asset_class="ACCIONES", settlement="A-24HS",
                 last=Decimal("100"), bid=Decimal("99"), ask=Decimal("101"),
                 bid_size=Decimal("10"), ask_size=Decimal("10"),
                 observed_at=observed_at, trade_at=trade_at, book_at=book_at,
                 last_kind=last_kind, currency=currency, market=market,
                 metadata_source="PPI_CATALOG:identity_only")


def emit(tmp_path, *quotes):
    recorder = ScannerPrimaryRecorder(tmp_path / FILENAME)
    for q in quotes:
        recorder.capture(q, request=(q.symbol, q.asset_class, q.settlement))
    assert not (tmp_path / FILENAME).exists(), "no per-symbol disk writes"
    receipt = recorder.publish()
    assert receipt["status"] == "PUBLISHED"
    return receipt


def test_reader_market_root_matches_observer_persistent_data_mount(tmp_path):
    # Docker binds host DATA to container /app/data. Both sides refer to the
    # same market directory; no independent path override may silently drift.
    import inspect
    import bf_production_paper_observer as observer
    market = tmp_path / "data" / "market"
    writer_path = market / FILENAME
    assert receipt_path(market) == writer_path
    assert "POROTA_SCANNER_PPI_PRIMARY_PATH" not in inspect.getsource(observer.run)
    writer = ScannerPrimaryRecorder(writer_path)
    writer.capture(quote(), request=("GGAL", "ACCIONES", "A-24HS"))
    writer.publish()
    assert primary_records(market, datetime.now(timezone.utc))[0]


def test_product_paper_snapshot_does_not_implicitly_prove_ppi_quote(tmp_path, monkeypatch):
    import scripts.rc6_full_universe_evidence as ledger
    import scripts.rc6_iol_shadow_collect as iol

    db = tmp_path / "observer.sqlite"
    q = quote()
    store = PaperStore(db)
    store.add_quote(q)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT source FROM market_snapshots").fetchone()[0] == SOURCE == "PRODUCTION_PAPER"
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    monkeypatch.setattr(ledger, "DB", str(db))
    monkeypatch.setattr(iol, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(iol, "DEFAULT_DB", str(db))
    monkeypatch.delenv("POROTA_SCANNER_PPI_PRIMARY_PATH", raising=False)
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    monkeypatch.delenv("POROTA_OBSERVER_DB", raising=False)
    monkeypatch.delenv("POROTA_IOL_OPERATIONAL_DB", raising=False)

    current = datetime.now(timezone.utc)
    assert ledger.primary_rows(current) == {}, "reproduces A425 provenance gap"
    raw_iol, _ = iol._primary_snapshot()
    assert raw_iol == {}

    receipt = emit(tmp_path, q)
    assert receipt["records"] == 1
    records, metadata = primary_records(tmp_path, current)
    assert len(records) == 1 and metadata["source_scope"] == "SCANNER_ROTATED_SUBSET_ONLY"
    assert records[0]["source"] == "PPI" and records[0]["provider_origin"] == ORIGIN
    assert records[0]["source_at"] == q.trade_at and records[0]["book_at"] == q.book_at
    assert "metadata_source" not in records[0], "catalog is not transport provenance"

    actual = ledger.primary_rows(current)
    key = ledger._exact_key({"symbol": q.symbol, "asset_class": q.asset_class,
                             "market": q.market, "currency": q.currency,
                             "settlement": q.settlement})
    assert key in actual and actual[key]["source"] == "PPI"
    proof = ledger.source_view(actual[key], "PPI", current, expected_identity=key)
    assert proof["present"] and proof["selection_eligible"]
    observed, meta = iol._primary_snapshot()
    assert observed["GGAL"]["provider_observed_at"] == q.trade_at
    assert meta["scanner_receipt_records"] == 1


def test_identity_ambiguous_symbol_keeps_exact_scanner_rows_but_iol_does_not_select(tmp_path, monkeypatch):
    import scripts.rc6_full_universe_evidence as ledger
    import scripts.rc6_iol_shadow_collect as iol
    emit(tmp_path, quote("SAME", currency="ARS"), quote("SAME", currency="USD"))
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    monkeypatch.setattr(ledger, "DB", str(tmp_path / "no-database.sqlite"))
    monkeypatch.setattr(iol, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(iol, "DEFAULT_DB", str(tmp_path / "no-database.sqlite"))
    monkeypatch.delenv("POROTA_SCANNER_PPI_PRIMARY_PATH", raising=False)
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    index = ledger.primary_rows(datetime.now(timezone.utc))
    keys = [key for key in index if isinstance(key, tuple) and key[0] == "SAME"]
    assert len(keys) == 2 and {key[3] for key in keys} == {"ARS", "USD"}
    assert index["SAME"]["canonical_identity"] is None
    values, metadata = iol._primary_snapshot()
    assert values == {}
    assert any(row["reason"] == "MULTIPLE_CURRENCY_MARKET_SETTLEMENT_IDENTITIES"
               for row in metadata["identity_reviews"])


def test_iol_scanner_overlay_cannot_launder_ambiguous_sqlite_ticker(tmp_path, monkeypatch):
    import scripts.rc6_iol_shadow_collect as iol
    emit(tmp_path, quote("SAME", currency="ARS"))
    db = tmp_path / "observer.sqlite"
    store = PaperStore(db)
    store.add_quote(quote("SAME", currency="ARS"))
    store.add_quote(quote("SAME", currency="USD"))
    # Construct the legacy independently verified PPI fallback fixture, not a
    # suggested mutation of the operational PRODUCTION_PAPER source column.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE market_snapshots SET source='PPI'")
    monkeypatch.setattr(iol, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(iol, "DEFAULT_DB", str(db))
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    values, meta = iol._primary_snapshot()
    assert "SAME" not in values
    assert any(row["reason"] == "MULTIPLE_CURRENCY_MARKET_SETTLEMENT_IDENTITIES"
               for row in meta["identity_reviews"])


@pytest.mark.parametrize("invalid", ["missing_trade", "missing_book", "future_native", "not_trade"])
def test_native_quote_clock_and_trade_type_are_fail_closed(tmp_path, invalid):
    now = datetime.now(timezone.utc) - timedelta(seconds=3)
    kwargs = {"trade_at": now.isoformat(), "book_at": now.isoformat()}
    if invalid == "missing_trade":
        kwargs["trade_at"] = ""
    if invalid == "missing_book":
        kwargs["book_at"] = ""
    if invalid == "future_native":
        kwargs["trade_at"] = (now + timedelta(minutes=5)).isoformat()
    if invalid == "not_trade":
        kwargs["last_kind"] = "UNAVAILABLE"
    emit(tmp_path, quote(**kwargs))
    records, _ = primary_records(tmp_path, datetime.now(timezone.utc))
    assert len(records) == 1 and records[0]["state"] == "UNAVAILABLE"
    from scripts.rc6_full_universe_evidence import source_view
    assert not source_view(records[0], "PPI", datetime.now(timezone.utc))["selection_eligible"]


def test_unknown_or_catalog_only_file_cannot_forge_quote_provenance(tmp_path):
    q = quote()
    row = {"symbol": q.symbol, "asset_class": q.asset_class, "market": q.market,
           "currency": q.currency, "settlement": q.settlement,
           "last": "100", "trade_at": q.trade_at, "book_at": q.book_at,
           "received_at": q.observed_at, "state": "READY", "source": "PPI_CATALOG",
           "provider_origin": "PPI_CATALOG",
           "canonical_identity": ["GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"]}
    path = tmp_path / FILENAME
    path.write_text(json.dumps({"schema": SCHEMA, "producer": PRODUCER,
                                "provider_origin": ORIGIN, "records": [row]}))
    assert primary_records(tmp_path, datetime.now(timezone.utc))[0] == []
    row["provider_origin"] = ORIGIN
    path.write_text(json.dumps({"schema": "UNKNOWN_SCHEMA", "producer": PRODUCER,
                                "provider_origin": ORIGIN, "records": [row]}))
    assert primary_records(tmp_path, datetime.now(timezone.utc))[0] == []


def test_static_catalog_cache_cannot_hide_absent_native_quote(tmp_path, monkeypatch):
    import scripts.rc6_full_universe_evidence as ledger
    import scripts.rc6_iol_shadow_collect as iol
    q = quote()
    payload = {"source": "PPI_CATALOG", "captured_at": q.observed_at,
               "quotes_by_symbol": {"GGAL": {
                   "symbol": "GGAL", "asset_class": "ACCIONES", "market": "BYMA",
                   "currency": "ARS", "settlement": "A-24HS", "last": 100,
                   "source_at": q.trade_at, "received_at": q.observed_at}}}
    (tmp_path / "primary_last.json").write_text(json.dumps(payload))
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    monkeypatch.setattr(ledger, "DB", str(tmp_path / "absent.sqlite"))
    monkeypatch.setattr(iol, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(iol, "DEFAULT_DB", str(tmp_path / "absent.sqlite"))
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    monkeypatch.delenv("POROTA_SCANNER_PPI_PRIMARY_PATH", raising=False)
    assert ledger.primary_rows(datetime.now(timezone.utc)) == {}
    assert iol._primary_snapshot()[0] == {}


def test_scanner_provider_receipt_ages_without_paper_promotion(tmp_path, monkeypatch):
    import scripts.rc6_full_universe_evidence as ledger
    q = quote()
    emit(tmp_path, q)
    monkeypatch.setattr(ledger, "ROOT", tmp_path)
    monkeypatch.setattr(ledger, "DB", str(tmp_path / "absent.sqlite"))
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    by_key = ledger.primary_rows(future)
    record = next(value for key, value in by_key.items() if isinstance(key, tuple))
    state = ledger.source_view(record, "PPI", future)
    assert state["present"] and state["freshness"]["state"] == "STALE"
    assert not state["selection_eligible"]


def test_a316_late_t1_scanner_also_emits_one_batch_receipt(tmp_path, monkeypatch):
    from zoneinfo import ZoneInfo
    import bf_production_paper_observer as observer
    observed = datetime(2026, 11, 2, 17, 30, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    q = Quote(symbol="GGAL", asset_class="ACCIONES", settlement="A-24HS",
              last=Decimal("100"), bid=Decimal("99"), ask=Decimal("101"),
              bid_size=Decimal("10"), ask_size=Decimal("10"), observed_at=observed.isoformat(),
              trade_at=observed.isoformat(), book_at=observed.isoformat(),
              last_kind="TRADE", currency="ARS", market="BYMA")
    store = PaperStore(tmp_path / "t1.sqlite")
    monkeypatch.setattr(observer, "_equity_t1_readonly_identities", lambda _store: [identity])
    monkeypatch.setattr(observer, "_option_t0_late_readonly_identities", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(observer, "_read_scanner_quote", lambda *_args, **_kwargs: q)
    class Reader:
        def current(self, *_args):
            raise AssertionError("test uses already-captured SDK fixture")
        book = current
    recorder = ScannerPrimaryRecorder(tmp_path / FILENAME)
    # Fixed historical clock: the test must remain runnable months from now.
    published_at = observed.astimezone(timezone.utc) + timedelta(seconds=1)
    original_publish = recorder.publish
    monkeypatch.setattr(recorder, "publish", lambda: original_publish(current=published_at))
    result = observer._equity_t1_readonly_tick(
        Reader(), store, now=observed, clock_fn=lambda: observed,
        scanner_primary=recorder)
    assert result["real_routes"] == [] and result["read"] == 1
    assert (tmp_path / FILENAME).is_file() and primary_records(tmp_path, published_at)[0]
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT source FROM market_snapshots").fetchone()[0] == "PRODUCTION_PAPER"


def test_explicit_request_mismatch_or_naive_receipt_is_not_published_as_ppi(tmp_path):
    recorder = ScannerPrimaryRecorder(tmp_path / FILENAME)
    recorder.capture(quote(), request=("OTHER", "ACCIONES", "A-24HS"))
    recorder.capture(quote(observed_at="2026-11-02T14:30:00"),
                     request=("GGAL", "ACCIONES", "A-24HS"))
    status = recorder.publish()
    assert status["records"] == 0
    assert status["invalid_identity"] == 1 and status["invalid_receipt_clock"] == 1


def test_atomic_receipt_is_bounded_and_rejects_symlink(tmp_path):
    emit(tmp_path, quote())
    records, meta = primary_records(tmp_path, datetime.now(timezone.utc))
    assert len(records) == 1 and meta["accepted_records"] == 1
    original = tmp_path / FILENAME
    original.rename(tmp_path / "real-receipt.json")
    original.symlink_to(tmp_path / "real-receipt.json")
    assert primary_records(tmp_path, datetime.now(timezone.utc))[0] == []
