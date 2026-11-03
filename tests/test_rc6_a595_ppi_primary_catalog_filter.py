"""A595: complementary catalog rows cannot veto a unique PPI provider request.

Before the fix this regression fails with
PPI_SCANNER_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS in the presence of one
IOL-only shadow row. All reads use an in-memory fake Reader, NEVER the provider.
"""
from pathlib import Path

import pytest

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
from tests.test_rc6_a550_ppi_scanner_identity_gate import (
    EXACT_ARS, NOW, REQUEST, Reader, _catalog, _store,
)


def _complement(store, currency):
    raw = {"ticker": "GGAL", "asset_type": "ACCIONES", "market": "BYMA",
           "currency": currency, "settlement": "A-24HS",
           "source": "IOL_COMPLEMENTARY"}
    record = catalog.normalize_complementary_record(
        raw, NOW, "A595_IOL_SHADOW_ONLY")
    assert record["settlement_source"] == "IOL_COMPLEMENTARY"
    with store.connect() as connection:
        catalog.persist(connection, record)
    return record


@pytest.mark.parametrize("currency", [None, "USD_CCL"])
def test_unique_ppi_with_complement_only_sibling_does_not_veto_ppi_get(
        tmp_path: Path, currency):
    store = _store(tmp_path)
    _catalog(store, "ARS")  # PPI SearchInstrument identity.
    complement = _complement(store, currency)
    if currency is None:
        assert complement["status"] == "OBSERVED_SHADOW"
    reader = Reader()
    quote = observer._read_scanner_quote(
        reader, store, *REQUEST, identity=EXACT_ARS)
    assert reader.calls == [("current", REQUEST), ("book", REQUEST)]
    assert reader.scopes[0]["identity"] == EXACT_ARS
    assert (quote.currency, quote.market) == ("ARS", "BYMA")


def test_two_ppi_primary_siblings_still_block_even_if_one_stale(tmp_path: Path):
    store = _store(tmp_path)
    _catalog(store, "ARS")
    _catalog(store, "USD_CCL", status="STALE")
    _complement(store, None)
    reader = Reader()
    with pytest.raises(ValueError, match="PPI_SCANNER_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS"):
        observer._read_scanner_quote(reader, store, *REQUEST, identity=EXACT_ARS)
    assert reader.calls == reader.scopes == []


def test_complement_alone_never_becomes_primary_identity(tmp_path: Path):
    store = _store(tmp_path)
    _complement(store, "ARS")
    reader = Reader()
    with pytest.raises(ValueError, match="PPI_SCANNER_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS"):
        observer._read_scanner_quote(reader, store, *REQUEST)
    assert reader.calls == reader.scopes == []
