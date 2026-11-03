"""A428: scanner PPI origin is distinct from PRODUCTION_PAPER execution mode."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import stat

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


def isolated_iol(monkeypatch, market_root, db=None):
    import scripts.rc6_iol_shadow_collect as iol
    monkeypatch.setattr(iol, "DEFAULT_ROOT", market_root)
    monkeypatch.setattr(iol, "DEFAULT_DB", str(db or market_root / "absent.sqlite"))
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    monkeypatch.delenv("POROTA_OBSERVER_DB", raising=False)
    monkeypatch.delenv("POROTA_IOL_OPERATIONAL_DB", raising=False)
    return iol


def isolated_ledger(monkeypatch, market_root, current):
    import scripts.rc6_full_universe_evidence as ledger
    monkeypatch.setattr(ledger, "ROOT", market_root)
    monkeypatch.setattr(ledger, "DB", str(market_root / "absent.sqlite"))
    monkeypatch.setattr(ledger, "now", lambda: current)
    monkeypatch.setattr(ledger, "catalog_rows", lambda: [{
        "symbol": "GGAL", "asset_class": "ACCIONES", "market": "BYMA", "currency": "ARS",
        "settlement": "A-24HS", "family": "ACCIONES", "capability": "READY_PAPER_SPOT",
    }])
    monkeypatch.setattr(ledger, "iol_rows", lambda: {})
    monkeypatch.setattr(ledger, "byma_rows", lambda: ({}, {}))
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    return ledger


def legacy_ppi_cache(tmp_path, *, row=None):
    now = datetime.now(timezone.utc) - timedelta(seconds=3)
    native = now.isoformat()
    item = {"symbol": "GGAL", "asset_class": "ACCIONES", "market": "BYMA",
            "currency": "ARS", "settlement": "A-24HS", "last": "100",
            "state": "READY", "last_kind": "TRADE", "source_at": native,
            "trade_at": native, "book_at": native, "received_at": native}
    item.update(row or {})
    (tmp_path / "primary_last.json").write_text(json.dumps({
        "source": "PPI", "market": "BYMA", "captured_at": native,
        "quotes_by_symbol": {"GGAL": item}}), encoding="utf-8")
    return item


def nest_legacy_ppi_cache(tmp_path, inner, *, top_source=None):
    path = tmp_path / "primary_last.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    outer = payload["quotes_by_symbol"]["GGAL"]
    outer["quote"] = inner
    if top_source is not None:
        outer["source"] = top_source
    path.write_text(json.dumps(payload), encoding="utf-8")


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


def test_private_receipt_matches_packaged_consumer_user_contract():
    # The observer image writes as UID 1000, while the packaged host reader
    # systemd unit has no User= (systemd root default). Runtime overrides need
    # a separate host inspection before deployment; a future unit change must
    # force a deliberate reconsideration of private receipt readability.
    project = Path(__file__).parents[1]
    docker = (project / "Dockerfile").read_text(encoding="utf-8")
    service = (project / "systemd/porota-iol-shadow-collector-rc6.service").read_text(
        encoding="utf-8")
    assert "USER botuser" in docker
    assert "ExecStart=/usr/bin/python3 /opt/porota-trading/scripts/rc6_source_cycle_dispatch.py" in service
    assert not any(line.strip().startswith("User=") for line in service.splitlines())


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


@pytest.mark.parametrize("invalid", ["missing_trade", "missing_book", "future_native", "future_book", "not_trade"])
def test_native_quote_clock_and_trade_type_are_fail_closed(tmp_path, monkeypatch, invalid):
    now = datetime.now(timezone.utc) - timedelta(seconds=3)
    kwargs = {"trade_at": now.isoformat(), "book_at": now.isoformat()}
    if invalid == "missing_trade":
        kwargs["trade_at"] = ""
    if invalid == "missing_book":
        kwargs["book_at"] = ""
    if invalid == "future_native":
        kwargs["trade_at"] = (now + timedelta(minutes=5)).isoformat()
    if invalid == "future_book":
        kwargs["book_at"] = (now + timedelta(minutes=5)).isoformat()
    if invalid == "not_trade":
        kwargs["last_kind"] = "UNAVAILABLE"
    emit(tmp_path, quote(**kwargs))
    records, meta = primary_records(tmp_path, datetime.now(timezone.utc))
    assert len(records) == 1 and records[0]["state"] == "UNAVAILABLE"
    assert meta["state"] == "UNAVAILABLE" and meta["accepted_records"] == 1
    assert meta["eligible_records"] == 0 and meta["native_unavailable_records"] == 1
    from scripts.rc6_full_universe_evidence import source_view
    assert not source_view(records[0], "PPI", datetime.now(timezone.utc))["selection_eligible"]
    values, snapshot = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    assert values == {} and snapshot["state"] == "UNAVAILABLE"
    assert snapshot["scanner_selectable_records"] == 0


def test_conflicting_native_receipt_is_diagnostic_only_in_both_readers(tmp_path, monkeypatch):
    first = quote()
    second = replace(first, bid=Decimal("98"))
    recorder = ScannerPrimaryRecorder(tmp_path / FILENAME)
    recorder.capture(first, request=(first.symbol, first.asset_class, first.settlement))
    recorder.capture(second, request=(second.symbol, second.asset_class, second.settlement))
    recorder.publish()
    rows, meta = primary_records(tmp_path, datetime.now(timezone.utc))
    assert len(rows) == 1 and rows[0]["state"] == "UNAVAILABLE"
    assert rows[0]["review_status"] == "CONFLICT_REVIEW_REQUIRED"
    assert meta["eligible_records"] == 0 and meta["state"] == "UNAVAILABLE"
    values, status = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    assert values == {} and status["scanner_selectable_records"] == 0


def test_scanner_book_must_be_fresh_independently_of_native_trade(tmp_path, monkeypatch):
    observed = datetime.now(timezone.utc) - timedelta(seconds=3)
    old_book = observed - timedelta(minutes=10)
    emit(tmp_path, quote(observed_at=observed.isoformat(), trade_at=observed.isoformat(),
                         book_at=old_book.isoformat()))
    persisted = json.loads((tmp_path / FILENAME).read_text(encoding="utf-8"))
    assert persisted["records"][0]["state"] == "UNAVAILABLE"
    assert persisted["native_unavailable_records"] == 1
    rows, meta = primary_records(tmp_path, datetime.now(timezone.utc))
    import scripts.rc6_full_universe_evidence as ledger
    current = datetime.now(timezone.utc)
    key = ledger._exact_key(rows[0])
    assert not ledger.source_view(rows[0], "PPI", current, expected_identity=key)["selection_eligible"]
    assert rows[0]["state"] == "UNAVAILABLE" and meta["eligible_records"] == 0
    values, snapshot = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    assert values == {} and snapshot["scanner_selectable_records"] == 0
    report = isolated_ledger(monkeypatch, tmp_path, current).build()
    actual = report["instruments"][0]
    assert not actual["sources"]["PPI"]["selection_eligible"]
    assert actual["status"] == "PENDING_EVIDENCE"
    assert actual["paper_contract_ready"] is False
    assert report["counts"]["paper_contract_ready"] == 0


def test_fresh_native_trade_and_book_survive_all_three_consumers(tmp_path, monkeypatch):
    current = datetime.now(timezone.utc)
    native = (current - timedelta(seconds=3)).isoformat()
    emit(tmp_path, quote(observed_at=native, trade_at=native, book_at=native))
    rows, meta = primary_records(tmp_path, current)
    assert len(rows) == meta["eligible_records"] == 1 and rows[0]["state"] == "READY"
    ledger = isolated_ledger(monkeypatch, tmp_path, current)
    key = ledger._exact_key(rows[0])
    assert ledger.source_view(rows[0], "PPI", current, expected_identity=key)["selection_eligible"]
    assert "GGAL" in isolated_iol(monkeypatch, tmp_path)._primary_snapshot()[0]
    report = ledger.build()
    item = report["instruments"][0]
    assert item["status"] == "READY_PAPER_PARTIAL_SOURCE"
    assert item["paper_contract_ready"] is True
    assert report["counts"]["paper_contract_ready"] == 1
    assert next(row for row in report["families"] if row["family"] == "ACCIONES")[
        "paper_contract_ready"] == 1


@pytest.mark.parametrize("missing_alias", [True, False])
def test_scanner_trade_source_alias_must_be_explicit_and_equal(tmp_path, missing_alias):
    emit(tmp_path, quote())
    path = tmp_path / FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    if missing_alias:
        payload["records"][0].pop("source_at")
    else:
        payload["records"][0]["source_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    records, summary = primary_records(tmp_path, datetime.now(timezone.utc))
    assert len(records) == 1 and records[0]["state"] == "UNAVAILABLE"
    assert summary["eligible_records"] == 0 and summary["state"] == "UNAVAILABLE"


@pytest.mark.parametrize("invalid", ["missing_trade", "missing_book", "future_book",
                                     "stale_book", "stale_trade", "zero_last", "naive_book",
                                     "mismatched_trade_alias", "not_trade", "nonfinite_last"])
def test_direct_legacy_ppi_fallback_must_not_bypass_native_quote_gate(invalid):
    import scripts.rc6_full_universe_evidence as ledger
    current = datetime.now(timezone.utc)
    recent = (current - timedelta(seconds=3)).isoformat()
    fallback = {"symbol": "GGAL", "asset_class": "ACCIONES", "market": "BYMA",
                "currency": "ARS", "settlement": "A-24HS", "source": "PPI",
                "last": 100, "last_kind": "TRADE", "state": "READY",
                "trade_at": recent, "source_at": recent, "book_at": recent,
                "received_at": recent}
    if invalid == "missing_trade":
        fallback["trade_at"] = ""
    elif invalid == "missing_book":
        fallback["book_at"] = ""
    elif invalid == "future_book":
        fallback["book_at"] = (current + timedelta(minutes=5)).isoformat()
    elif invalid == "stale_book":
        fallback["book_at"] = (current - timedelta(minutes=10)).isoformat()
    elif invalid == "stale_trade":
        fallback["source_at"] = fallback["trade_at"] = (current - timedelta(minutes=10)).isoformat()
    elif invalid == "zero_last":
        fallback["last"] = 0
    elif invalid == "naive_book":
        fallback["book_at"] = "2026-11-02T12:00:00"
    elif invalid == "mismatched_trade_alias":
        fallback["source_at"] = (current - timedelta(seconds=95)).isoformat()
    elif invalid == "not_trade":
        fallback["last_kind"] = "BID"
    elif invalid == "nonfinite_last":
        fallback["last"] = "NaN"
    key = ledger._exact_key(fallback)
    assert not ledger.source_view(fallback, "PPI", current, expected_identity=key)[
        "selection_eligible"]


@pytest.mark.parametrize("invalid", ["book_missing", "book_stale", "book_future",
                                     "zero_last", "nonfinite_last", "unavailable_state",
                                     "false_state", "trade_mismatch", "review_conflict",
                                     "complement_origin", "catalog_origin"])
def test_iol_cache_fallback_without_scanner_must_fail_closed(tmp_path, monkeypatch, invalid):
    current = datetime.now(timezone.utc)
    stale = (current - timedelta(minutes=10)).isoformat()
    future = (current + timedelta(minutes=5)).isoformat()
    variants = {
        "book_missing": {"book_at": ""},
        "book_stale": {"book_at": stale},
        "book_future": {"book_at": future},
        "zero_last": {"last": 0},
        "nonfinite_last": {"last": "NaN"},
        "unavailable_state": {"state": "UNAVAILABLE"},
        "false_state": {"state": False},
        "trade_mismatch": {"trade_at": stale},
        "review_conflict": {"review_status": "CONFLICT_REVIEW_REQUIRED"},
        "complement_origin": {"source": "IOL"},
        "catalog_origin": {"source": "PPI_CATALOG"},
    }
    legacy_ppi_cache(tmp_path, row=variants[invalid])
    values, meta = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    assert values == {} and meta["state"] == "UNAVAILABLE"
    assert any(review["reason"] == "PPI_FALLBACK_NATIVE_QUOTE_INELIGIBLE"
               for review in meta["identity_reviews"])


def test_iol_cache_fallback_with_true_native_book_remains_available(tmp_path, monkeypatch):
    row = legacy_ppi_cache(tmp_path)
    values, meta = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    assert values["GGAL"]["provider_observed_at"] == row["trade_at"]
    assert meta["state"] == "READY"


def test_iol_sqlite_fallback_missing_native_book_does_not_select(tmp_path, monkeypatch):
    db = tmp_path / "invalid_ppi.sqlite"
    PaperStore(db).add_quote(quote(book_at=""))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE market_snapshots SET source='PPI'")
    values, meta = isolated_iol(monkeypatch, tmp_path, db)._primary_snapshot()
    assert values == {} and meta["state"] == "UNAVAILABLE"
    assert any(review["reason"] == "PPI_FALLBACK_NATIVE_QUOTE_INELIGIBLE"
               for review in meta["identity_reviews"])


def test_iol_sqlite_newer_invalid_receipt_suppresses_older_exact_quote(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    old, new = (now - timedelta(seconds=40)).isoformat(), (now - timedelta(seconds=3)).isoformat()
    db = tmp_path / "invalid_after_valid_ppi.sqlite"
    store = PaperStore(db)
    store.add_quote(quote(observed_at=old, trade_at=old, book_at=old))
    store.add_quote(quote(observed_at=new, trade_at=new, book_at=""))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE market_snapshots SET source='PPI'")
    values, meta = isolated_iol(monkeypatch, tmp_path, db)._primary_snapshot()
    assert values == {} and meta["state"] == "UNAVAILABLE"
    assert any(row["reason"] == "NEWER_PPI_FALLBACK_UNAVAILABLE_SUPPRESSES_PRIOR"
               for row in meta["identity_reviews"])


@pytest.mark.parametrize("invalid_is_newer", [True, False])
def test_iol_fallback_scanner_mixed_invalid_receipt_never_wins(tmp_path, monkeypatch,
                                                                invalid_is_newer):
    current = datetime.now(timezone.utc)
    old, new = (current - timedelta(seconds=40)).isoformat(), (current - timedelta(seconds=3)).isoformat()
    scanner_at, fallback_at = (old, new) if invalid_is_newer else (new, old)
    emit(tmp_path, quote(observed_at=scanner_at, trade_at=scanner_at, book_at=scanner_at))
    legacy_ppi_cache(tmp_path, row={"received_at": fallback_at, "trade_at": fallback_at,
                                    "source_at": fallback_at, "book_at": ""})
    values, meta = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    if invalid_is_newer:
        assert values == {} and meta["state"] == "UNAVAILABLE"
    else:
        assert values["GGAL"]["provider_observed_at"] == scanner_at


def test_iol_ambiguous_fallback_identity_does_not_resurrect_scanner(tmp_path, monkeypatch):
    emit(tmp_path, quote(currency="ARS"))
    legacy_ppi_cache(tmp_path, row={"currency": "USD", "book_at": ""})
    values, meta = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    assert values == {}
    assert any(row["reason"] == "MULTIPLE_CURRENCY_MARKET_SETTLEMENT_IDENTITIES"
               for row in meta["identity_reviews"])


@pytest.mark.parametrize("outer_source", [None, "PPI"])
def test_iol_nested_complement_quote_must_not_launder_ppi_authority(
        tmp_path, monkeypatch, outer_source):
    root = legacy_ppi_cache(tmp_path)
    inner = {**root, "source": "IOL"}
    nest_legacy_ppi_cache(tmp_path, inner, top_source=outer_source)
    values, meta = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    assert values == {} and meta["state"] == "UNAVAILABLE"
    assert any(review["reason"] == "PPI_NESTED_QUOTE_AUTHORITY_OR_IDENTITY_CONFLICT"
               for review in meta["identity_reviews"])


@pytest.mark.parametrize("invalid", ["missing_nested_source", "currency_mismatch",
                                     "book_stale_inside", "last_zero_inside", "clock_conflict"])
def test_iol_nested_ppi_fallback_requires_own_exact_native_evidence(tmp_path, monkeypatch,
                                                                      invalid):
    root = legacy_ppi_cache(tmp_path)
    inner = {**root, "source": "PPI"}
    if invalid == "missing_nested_source":
        inner.pop("source")
    elif invalid == "currency_mismatch":
        inner["currency"] = "USD"
    elif invalid == "book_stale_inside":
        inner["book_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    elif invalid == "last_zero_inside":
        inner["last"] = "0"
    elif invalid == "clock_conflict":
        inner["trade_at"] = (datetime.now(timezone.utc) - timedelta(seconds=40)).isoformat()
    nest_legacy_ppi_cache(tmp_path, inner, top_source="PPI")
    values, meta = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    assert values == {} and meta["state"] == "UNAVAILABLE"
    assert any(review["reason"] == "PPI_NESTED_QUOTE_AUTHORITY_OR_IDENTITY_CONFLICT"
               for review in meta["identity_reviews"])


@pytest.mark.parametrize("top_source", [None, "PPI"])
def test_iol_nested_exact_ppi_native_quote_remains_eligible(tmp_path, monkeypatch,
                                                             top_source):
    root = legacy_ppi_cache(tmp_path)
    inner = {**root, "source": "PPI"}
    nest_legacy_ppi_cache(tmp_path, inner, top_source=top_source)
    values, meta = isolated_iol(monkeypatch, tmp_path)._primary_snapshot()
    assert values["GGAL"]["provider_observed_at"] == root["trade_at"]
    assert meta["state"] == "READY"


def test_newer_unavailable_scanner_blocks_older_same_identity_fallback(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    earlier, later = now - timedelta(seconds=40), now - timedelta(seconds=5)
    previous = quote(observed_at=earlier.isoformat(), trade_at=earlier.isoformat(),
                     book_at=earlier.isoformat())
    db = tmp_path / "old_ppi.sqlite"
    PaperStore(db).add_quote(previous)
    with sqlite3.connect(db) as conn:
        # Legacy PPI fallback fixture only: NEVER change operational source.
        conn.execute("UPDATE market_snapshots SET source='PPI'")
    emit(tmp_path, quote(observed_at=later.isoformat(), trade_at=later.isoformat(),
                         book_at=""))
    values, meta = isolated_iol(monkeypatch, tmp_path, db)._primary_snapshot()
    assert values == {} and meta["scanner_older_fallbacks_suppressed"] == 1
    assert meta["scanner_selectable_records"] == 0


def test_newer_verified_fallback_survives_older_scanner_unavailable(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    earlier, later = now - timedelta(seconds=40), now - timedelta(seconds=5)
    emit(tmp_path, quote(observed_at=earlier.isoformat(), trade_at=earlier.isoformat(),
                         book_at=""))
    verified = quote(observed_at=later.isoformat(), trade_at=later.isoformat(),
                     book_at=later.isoformat())
    db = tmp_path / "new_ppi.sqlite"
    PaperStore(db).add_quote(verified)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE market_snapshots SET source='PPI'")
    values, meta = isolated_iol(monkeypatch, tmp_path, db)._primary_snapshot()
    assert values["GGAL"]["provider_observed_at"] == verified.trade_at
    assert meta["scanner_older_fallbacks_suppressed"] == 0


def test_unavailable_alternate_currency_still_blocks_ambiguous_ticker(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc) - timedelta(seconds=3)
    emit(tmp_path, quote("SAME", currency="USD", observed_at=now.isoformat(),
                         trade_at=now.isoformat(), book_at=""))
    db = tmp_path / "ambiguous_ppi.sqlite"
    PaperStore(db).add_quote(quote("SAME", currency="ARS", observed_at=now.isoformat(),
                                  trade_at=now.isoformat(), book_at=now.isoformat()))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE market_snapshots SET source='PPI'")
    values, meta = isolated_iol(monkeypatch, tmp_path, db)._primary_snapshot()
    assert "SAME" not in values
    assert any(review["reason"] == "MULTIPLE_CURRENCY_MARKET_SETTLEMENT_IDENTITIES"
               for review in meta["identity_reviews"])


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
    assert len(records) == 1 and meta["accepted_records"] == meta["eligible_records"] == 1
    original = tmp_path / FILENAME
    assert stat.S_IMODE(original.stat().st_mode) == 0o600
    original.rename(tmp_path / "real-receipt.json")
    original.symlink_to(tmp_path / "real-receipt.json")
    assert primary_records(tmp_path, datetime.now(timezone.utc))[0] == []
