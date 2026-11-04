"""A550: a three-key PPI Book cannot attest an ambiguous five-key identity.

No API, network, budget writer, order or host access in these tests.
"""
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
from be_paper_engine import PaperStore


NOW = "2026-11-03T13:50:00-03:00"
REQUEST = ("GGAL", "ACCIONES", "A-24HS")
EXACT_ARS = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
EXACT_USD = ("GGAL", "ACCIONES", "BYMA", "USD", "A-24HS")


class Reader:
    def __init__(self):
        self.calls = []
        self.scopes = []

    def read_scope(self, **scope):
        self.scopes.append(scope)
        return nullcontext()

    def current(self, *args):
        self.calls.append(("current", args))
        return {"date": NOW, "price": 100}

    def book(self, *args):
        self.calls.append(("book", args))
        return {"date": NOW,
                "bids": [{"price": 99, "quantity": 10}],
                "offers": [{"price": 101, "quantity": 12}]}


def _store(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    return store


def _catalog(store, currency, *, market="BYMA", status="AVAILABLE"):
    record = catalog.normalize_record(
        {"ticker": "GGAL", "type": "ACCIONES", "market": market,
         "currency": currency}, "A-24HS", NOW, "a550-ppi-fixture")
    record["status"] = status
    with store.connect() as connection:
        catalog.persist(connection, record)


class ScannerFullIdentityGuardTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rc6-a550-")
        self.addCleanup(temp.cleanup)
        self.store = _store(Path(temp.name))

    def test_unique_catalog_identity_allows_one_current_and_book(self):
        _catalog(self.store, "ARS")
        reader = Reader()
        quote = observer._read_scanner_quote(reader, self.store, *REQUEST)
        self.assertEqual(reader.calls, [("current", REQUEST), ("book", REQUEST)])
        self.assertEqual(reader.scopes[0]["identity"], EXACT_ARS)
        self.assertEqual(quote.monetary_identity(), ("ARS", "BYMA"))
        self.assertEqual((quote.symbol, quote.asset_class, quote.market,
                          quote.currency, quote.settlement), EXACT_ARS)
        self.assertEqual((quote.book_at, quote.trade_at), (NOW, NOW))

    def test_non_ready_catalog_sibling_blocks_ppi_get_even_with_one_ready_candidate(self):
        _catalog(self.store, "ARS")
        # A consumer sees just one READY candidate, but PPI's 3-key request
        # has no currency parameter. A stale catalog sibling is still relevant.
        with self.store.connect() as connection:
            catalog.sync_candidate_universe(connection, NOW)
        _catalog(self.store, "USD", status="STALE")
        with self.store.connect() as connection:
            count = connection.execute("""SELECT COUNT(*) FROM candidate_identity_v2
              WHERE ticker='GGAL' AND instrument_type='ACCIONES'
                AND settlement='A-24HS' AND status='AVAILABLE'""").fetchone()[0]
        self.assertEqual(count, 1)
        reader = Reader()
        with self.assertRaisesRegex(ValueError, "PPI_SCANNER_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS"):
            observer._read_scanner_quote(reader, self.store, *REQUEST,
                                         identity=EXACT_ARS)
        self.assertEqual((reader.calls, reader.scopes), ([], []))

    def test_opened_explicit_currency_mismatch_never_spends_provider_request(self):
        _catalog(self.store, "ARS")
        reader = Reader()
        with self.assertRaisesRegex(ValueError, "PPI_SCANNER_OPENED_IDENTITY_MISMATCH"):
            observer._read_scanner_quote(reader, self.store, *REQUEST,
                                         priority="OPENED_CRITICAL", identity=EXACT_USD)
        self.assertEqual((reader.calls, reader.scopes), ([], []))

    def test_market_sibling_with_same_currency_also_blocks_provider_get(self):
        _catalog(self.store, "ARS")
        _catalog(self.store, "ARS", market="NASDAQ", status="STALE")
        reader = Reader()
        with self.assertRaisesRegex(ValueError, "PPI_SCANNER_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS"):
            observer._read_scanner_quote(reader, self.store, *REQUEST,
                                         identity=EXACT_ARS)
        self.assertEqual((reader.calls, reader.scopes), ([], []))

    def test_malformed_opened_identity_never_reaches_provider(self):
        _catalog(self.store, "ARS")
        reader = Reader()
        with self.assertRaisesRegex(ValueError, "PPI_SCANNER_OPENED_IDENTITY_INVALID"):
            observer._read_scanner_quote(reader, self.store, *REQUEST,
                                         priority="OPENED_CRITICAL", identity="GGAL")
        self.assertEqual((reader.calls, reader.scopes), ([], []))

    def test_opened_critical_missing_full_identity_never_falls_back_to_catalog(self):
        _catalog(self.store, "ARS")
        reader = Reader()
        with self.assertRaisesRegex(ValueError, "PPI_SCANNER_OPENED_IDENTITY_UNVERIFIED"):
            observer._read_scanner_quote(reader, self.store, *REQUEST,
                                         priority="OPENED_CRITICAL", identity=None)
        self.assertEqual((reader.calls, reader.scopes), ([], []))

    def test_missing_catalog_is_no_provider_no_go(self):
        reader = Reader()
        with self.assertRaisesRegex(ValueError, "PPI_SCANNER_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS"):
            observer._read_scanner_quote(reader, self.store, *REQUEST)
        self.assertEqual((reader.calls, reader.scopes), ([], []))


if __name__ == "__main__":
    unittest.main()
