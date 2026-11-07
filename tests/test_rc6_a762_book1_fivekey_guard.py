"""A762 R4 scanner compatibility and adversarial five-key witness tests.

These execute only two extracted observer functions with real PPI journal code,
synthetic SQLite catalog and fake Current/Book reader. No provider network.
"""
import ast
from contextlib import nullcontext
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from rc6_ppi_scanner_provenance import (
    ScannerPrimaryRecorder, PIT_JOURNAL_FILENAME, verified_native_book_journal,
)

IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
BOOK_AT = "2026-11-07T11:00:00-03:00"
OBSERVED = "2026-11-07T11:00:01-03:00"


@dataclass(frozen=True)
class Quote:
    symbol: str
    asset_class: str
    settlement: str
    last: Decimal
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    observed_at: str
    currency: str
    market: str
    book_at: str
    trade_at: str
    ppi_book1_event_sha256: str | None = None

    def monetary_identity(self):
        return self.currency, self.market

    def time_error(self, as_of, *, require_trade=False):
        return None


def load_real_observer_functions():
    script = Path(__file__).resolve().parents[1] / "bf_production_paper_observer.py"
    parsed = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    nodes = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
             and node.name in {"_ppi_native_book_identity", "_read_scanner_quote"}]
    if len(nodes) != 2:
        raise AssertionError("Expected both authentic R4 observer functions")
    def lookup(store, symbol, family, settlement, *, market=None, currency=None):
        return {"market": market or "BYMA", "currency": currency or "ARS"}
    def normalize(symbol, family, settlement, current, book, *, metadata):
        return Quote(symbol, family, settlement, Decimal(current["price"]),
                     Decimal(book["bids"][0]["price"]),
                     Decimal(book["offers"][0]["price"]),
                     Decimal(book["bids"][0]["quantity"]),
                     Decimal(book["offers"][0]["quantity"]), OBSERVED,
                     metadata["currency"], metadata["market"], book["date"], current["date"])
    scope = {
        "__builtins__": __builtins__, "sqlite3": sqlite3, "nullcontext": nullcontext,
        "replace": replace, "retry_read": lambda fn, retries: fn(),
        "now_iso": lambda: OBSERVED, "normalize_quote": normalize,
        "financial_catalog": SimpleNamespace(lookup=lookup),
    }
    exec(compile(ast.Module(nodes, type_ignores=[]),str(script),"exec"),scope)
    return scope["_read_scanner_quote"]


class Reader:
    def __init__(self):
        self.calls = []

    def read_scope(self, **kwargs):
        return nullcontext()

    def current(self, *request):
        self.calls.append(("current",request))
        return {"price": "100", "date": BOOK_AT}

    def book(self,*request):
        self.calls.append(("book", request))
        return {"date":BOOK_AT,
                "bids":[{"position":0,"price":"99.5","quantity":"10"}],
                "offers":[{"position":0,"price":"101","quantity":"11"}]}


class Store:
    def __init__(self, path):
        self.path=path
        self.events=[]

    def connect(self):
        return sqlite3.connect(self.path)

    def event(self, kind, detail):
        self.events.append((kind,detail))


class TestA762NativeFiveKey(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="a762-scanner-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.store = Store(root / "paper.db")
        with self.store.connect() as c:
            c.execute("""CREATE TABLE financial_instrument_catalog(
                 ticker TEXT, instrument_type TEXT, settlement TEXT,
                 market TEXT, currency TEXT, settlement_source TEXT)""")
        self.journal_dir = root / "market"
        self.journal_dir.mkdir()
        self.recorder = ScannerPrimaryRecorder(self.journal_dir / "latest.json")
        self.journal = self.journal_dir / PIT_JOURNAL_FILENAME
        self.read = load_real_observer_functions()

    def primary(self, *, currency="ARS", market="BYMA"):
        with self.store.connect() as c:
            c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?)",
                      (IDENTITY[0], IDENTITY[1], IDENTITY[4],
                       market, currency, "PPI_FIELD"))

    def run_read(self, *, identity=IDENTITY, priority="DISCOVERY"):
        reader = Reader()
        quote = self.read(reader,self.store,"GGAL","ACCIONES","A-24HS",
                          identity=identity,priority=priority,
                          scanner_primary=self.recorder)
        self.assertEqual(reader.calls,[("current",("GGAL","ACCIONES","A-24HS")),
                                       ("book",("GGAL","ACCIONES","A-24HS"))])
        return quote

    def test_unique_primary_pins_book1_without_extra_get(self):
        self.primary()
        quote=self.run_read()
        rows,status=verified_native_book_journal(self.journal)
        self.assertEqual(status["state"],"HASH_CHAIN_VERIFIED")
        self.assertEqual(len(rows),1)
        self.assertEqual(quote.ppi_book1_event_sha256,rows[0]["event_sha256"])

    def test_ambiguous_currency_refuses_book1_but_scanner_still_reads(self):
        self.primary()
        self.primary(currency="USD")
        quote=self.run_read()
        self.assertIsNone(quote.ppi_book1_event_sha256)
        self.assertFalse(self.journal.exists())

    def test_no_primary_catalog_refuses_book1_without_changing_scanner(self):
        quote=self.run_read()
        self.assertIsNone(quote.ppi_book1_event_sha256)
        self.assertFalse(self.journal.exists())

    def test_duplicate_catalog_claim_refuses_book1_even_same_currency(self):
        self.primary()
        self.primary()
        quote=self.run_read()
        self.assertIsNone(quote.ppi_book1_event_sha256)
        self.assertFalse(self.journal.exists())

    def test_scope_conflict_refuses_book1_without_extra_get(self):
        self.primary()
        quote=self.run_read(identity=("GGAL","ACCIONES","BYMA","USD","A-24HS"))
        self.assertIsNone(quote.ppi_book1_event_sha256)
        self.assertFalse(self.journal.exists())

    def test_ambiguous_opened_budget_keeps_exit_reader_but_not_book1(self):
        self.primary()
        quote=self.run_read(identity=None, priority="OPENED_CRITICAL")
        self.assertIsNone(quote.ppi_book1_event_sha256)
        self.assertFalse(self.journal.exists())


if __name__ == "__main__":
    unittest.main()
