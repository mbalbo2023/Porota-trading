"""A606: bounded native PPI diagnostics never become PAPER authority.

Every identity is synthetic, every store is temporary, and no provider client
is instantiated. The real observer SDK call count is unchanged because only
one existing cold request can be replaced.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import inspect
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
from rc6_performance.scanner import sampling_plan
from rc6_ppi_scanner_provenance import (FILENAME, PIT_JOURNAL_FILENAME,
                                        ScannerPrimaryRecorder,
                                        verified_native_book_journal)


AT = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
FAMILIES = observer.PPI_OBSERVE_ONLY_FAMILIES


class FakeStore:
    def __init__(self, root: Path):
        self.path = str(root / "synthetic-observer.db")
        self.events = []
        with self.connect() as c:
            c.executescript("""CREATE TABLE universe_cycle_metrics(
              id INTEGER PRIMARY KEY AUTOINCREMENT, cursor_after INTEGER,
              detail TEXT NOT NULL);""")
        catalog.init_schema(self)
        with self.connect() as c:
            c.execute("""CREATE TABLE candidate_identity_v2(
              ticker TEXT NOT NULL, instrument_type TEXT NOT NULL, market TEXT NOT NULL,
              currency TEXT NOT NULL, settlement TEXT NOT NULL,
              can_simulate INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
              checked_at TEXT NOT NULL,
              PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def open_positions(self):
        return []

    def event(self, *args):
        self.events.append(args)

    def row(self, ticker, family, *, source="PPI_PRIMARY", settlement_source="PPI_FIELD",
            currency="ARS", status="AVAILABLE", can_simulate=0, settlement="A-24HS"):
        market = "A3" if family == "FUTUROS" else "BYMA"
        record = {"ticker": ticker, "instrument_type": family,
                  "market": market, "currency": currency, "settlement": settlement,
                  "settlement_source": settlement_source, "description": "synthetic",
                  "last_seen_at": AT.isoformat(), "run_id": "synthetic-a606",
                  "status": status, "capability": "NEEDS_CONTRACT",
                  "raw": {"_discovery_source": source}}
        with self.connect() as c:
            catalog.persist(c, record)
            c.execute("""INSERT OR REPLACE INTO candidate_identity_v2
              VALUES(?,?,?,?,?,?,?,?,?)""", (
                ticker, family, market, currency, settlement, can_simulate,
                "AVAILABLE" if can_simulate else "PAUSED_EXPLICIT",
                "READY_PAPER_SPOT" if can_simulate else "NEEDS_CONTRACT",
                AT.isoformat()))
        return (ticker, family, settlement)

    def checkpoint(self, after, plan):
        with self.connect() as c:
            c.execute("INSERT INTO universe_cycle_metrics(cursor_after,detail) VALUES(?,?)",
                (after, json.dumps({"active_plan": plan}, sort_keys=True)))


class OffBaseline:
    def state(self, _at):
        return {"status": "OFF_BASELINE"}


class A606Tests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="a606-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.store = FakeStore(Path(self.temp.name))
        self.ready = [self.store.row(f"READY{i:03d}", "ACCIONES", can_simulate=1)
                      for i in range(26)]
        self.diag = [self.store.row(f"A606{family}", family) for family in FAMILIES]
        self.patches = [
            patch.object(observer, "ACTIVE_SYMBOL_LIMIT", 6),
            patch.object(observer, "INTERVAL", 5),
            patch.object(observer, "PPI_CALL_BUDGET_SECONDS", .1),
            # Synthetic 15-minute/100-sample window caps the durable warm
            # basket below 26 READY rows, leaving one actual cold slot.
            patch.object(observer, "SIGNAL_MIN_SAMPLES", 100),
            patch.object(observer, "SIGNAL_WINDOW_MINUTES", 15),
            patch.object(observer, "FOCUS_SYMBOLS", ()),
            patch.object(observer, "now_iso", lambda: AT.isoformat()),
            patch("rc6_dynamic_universe.promotion.capacity_controller_from_environment",
                  return_value=OffBaseline()),
        ]
        for patched in self.patches:
            patched.start()
            self.addCleanup(patched.stop)

    def call_plan(self):
        return observer._cycle_plan(self.store)

    def test_nine_family_rotation_uses_one_cold_pair_only_each_third_cycle(self):
        observed = []
        normal_cold = 0
        for cycle in range(len(FAMILIES) * observer.PPI_OBSERVE_ONLY_COLD_PERIOD):
            selected, total, before, after, plan = self.call_plan()
            self.assertEqual(total, 26)
            self.assertEqual(len(selected), observer.ACTIVE_SYMBOL_LIMIT)
            self.assertTrue(plan["cold"])
            self.assertEqual(len(plan["cold"]), 1)
            self.assertEqual(plan["opening_identities"], plan["pinned"] + plan["warm"])
            diagnostic = plan["ppi_observe_only_requests"]
            if diagnostic:
                self.assertEqual(cycle % observer.PPI_OBSERVE_ONLY_COLD_PERIOD, 0)
                self.assertEqual(len(diagnostic), 1)
                request, identity = tuple(diagnostic[0]["request"]), tuple(diagnostic[0]["identity"])
                self.assertEqual(request, (identity[0], identity[1], identity[4]))
                self.assertEqual(observer._ppi_observe_only_identity(request, plan), identity)
                self.assertIn(request, selected)
                self.assertNotIn(tuple(plan["ppi_observe_only_replaced_cold"]), selected)
                self.assertEqual(after, before)  # do not skip an unobserved cold
                observed.append(identity[1])
            else:
                self.assertNotEqual(cycle % observer.PPI_OBSERVE_ONLY_COLD_PERIOD, 0)
                normal_cold += 1
                self.assertIn(tuple(plan["cold"][0]), selected)
            self.store.checkpoint(after, plan)
        self.assertEqual(observed, list(FAMILIES))
        self.assertEqual(normal_cold, 2 * len(FAMILIES))
        self.assertNotIn("ETFS", observed)  # operator's explicit HOLD

    def test_provider_request_replacement_never_adds_a_call_or_displaces_opened(self):
        universe = list(observer._eligible_symbols(self.store))
        base, _, _, p = sampling_plan(universe, (), (),
            limit=6, cycle_seconds=6.2, required_samples=observer.SIGNAL_MIN_SAMPLES,
            window_seconds=observer.SIGNAL_WINDOW_MINUTES * 60, now=AT.isoformat())
        chosen, _, _, _, plan = self.call_plan()
        self.assertEqual(len(chosen), len(base))
        self.assertEqual(len(plan["cold"]), len(p["cold"]))
        self.assertEqual(plan["pinned"], p["pinned"])
        self.assertEqual(plan["warm"], p["warm"])
        self.assertEqual(len(set(chosen)), len(chosen))
        self.assertEqual(len(plan["ppi_observe_only_requests"]), 1)
        # No unbounded extra reader.current/book is reachable from the
        # selector; it produces requests only and does no provider I/O.
        for func in (observer._ppi_observe_only_family_candidates,
                     observer._ppi_observe_only_replace_cold):
            source = inspect.getsource(func)
            self.assertNotIn("reader.current(", source)
            self.assertNotIn("reader.book(", source)

    def test_complementary_only_and_ambiguous_ppi_siblings_fail_closed(self):
        self.store.row("A000COMP", "FCI", source="IOL_COMPLEMENTARY",
                       settlement_source="PPI_FIELD")
        self.store.row("A000LEGACY", "FCI", source="LEGACY_CATALOG")
        self.store.row("A000GOOD", "FCI", source="PPI_PRIMARY")
        self.store.row("A000GOOD", "FCI", source="PPI_PRIMARY", currency="USD",
                       status="STALE")
        candidates = observer._ppi_observe_only_family_candidates(
            self.store, as_of=AT.isoformat(), excluded=set())
        self.assertEqual(candidates["FCI"]["identity"][0], "A606FCI")
        for family in FAMILIES:
            self.assertEqual(candidates[family]["state"], "PPI_PRIMARY_EXACT_OBSERVE_ONLY")
        with self.store.connect() as c:
            c.execute("DELETE FROM financial_instrument_catalog WHERE ticker='A606FCI'")
        candidates = observer._ppi_observe_only_family_candidates(
            self.store, as_of=AT.isoformat(), excluded=set())
        self.assertEqual(candidates["FCI"]["state"], "PPI_PROVIDER_REQUEST_AMBIGUOUS")

    def test_unknown_family_identity_is_explicit_and_no_extra_get(self):
        with self.store.connect() as c:
            c.execute("DELETE FROM financial_instrument_catalog WHERE instrument_type='FUTUROS'")
        states = observer._ppi_observe_only_family_candidates(
            self.store, as_of=AT.isoformat(), excluded=set())
        self.assertEqual(states["FUTUROS"], {"state": "NO_PROVIDER_IDENTITY"})
        selected, _, _, _, plan = self.call_plan()
        self.assertEqual(plan["ppi_observe_only_family_states"]["FUTUROS"], "NO_PROVIDER_IDENTITY")
        self.assertEqual(len(selected), observer.ACTIVE_SYMBOL_LIMIT)

    def test_book_observation_does_not_promote_contract_or_pit(self):
        chosen, _, _, _, plan = self.call_plan()
        request = tuple(plan["ppi_observe_only_requests"][0]["request"])
        self.assertIn(request, chosen)
        with self.store.connect() as c:
            before = c.execute("""SELECT can_simulate,status FROM candidate_identity_v2
              WHERE ticker=? AND instrument_type=? AND settlement=?""", request).fetchone()
        self.assertEqual(tuple(before), (0, "PAUSED_EXPLICIT"))
        # The real loop publishes the scanner's native record first, but its
        # diagnostics branch continues before store.add_quote and broker.
        body = inspect.getsource(observer.run)
        early = body.index('if diagnostic_identity is not None:',
                           body.index('scanner_primary.capture(q'))
        skip = body.index('continue', early)
        broker = body.index('broker.on_quote(', early)
        self.assertLess(skip, broker)
        self.assertLess(skip, body.index('store.add_quote(q)', early))
        with self.store.connect() as c:
            after = c.execute("""SELECT can_simulate,status FROM candidate_identity_v2
              WHERE ticker=? AND instrument_type=? AND settlement=?""", request).fetchone()
        self.assertEqual(tuple(after), tuple(before))
        self.assertNotIn(request, observer._eligible_symbols(self.store))

    def test_incomplete_hold_gets_same_native_ppi_book_without_paper_authority(self):
        selected, _, _, _, plan = self.call_plan()
        observation = plan["ppi_observe_only_requests"][0]
        request, identity = tuple(observation["request"]), tuple(observation["identity"])
        self.assertEqual(request, ("A606ACCIONES", "ACCIONES", "A-24HS"))
        native_at = (AT - timedelta(seconds=2)).isoformat()
        book = {"date": native_at,
                "bids": [{"position": 0, "price": "99", "quantity": "10"}],
                "offers": [{"position": 0, "price": "101", "quantity": "11"}]}

        class LocalReader:
            def __init__(self):
                self.calls = []

            def read_scope(self, **kwargs):
                self.calls.append(("scope", kwargs))
                return nullcontext()

            def current(self, *args):
                self.calls.append(("current", args))
                return {"date": native_at, "price": "100"}

            def book(self, *args):
                self.calls.append(("book", args))
                return book

        reader = LocalReader()
        recorder = ScannerPrimaryRecorder(Path(self.temp.name) / "market" / FILENAME)
        quote = observer._read_scanner_quote(
            reader, self.store, *request, priority="DISCOVERY", identity=identity,
            scanner_primary=recorder)
        recorder.capture(quote, request=request)
        self.assertEqual(reader.calls,
            [("scope", {"priority": "DISCOVERY", "identity": identity}),
             ("current", request), ("book", request)])
        rows, receipt = verified_native_book_journal(
            Path(self.temp.name) / "market" / PIT_JOURNAL_FILENAME)
        self.assertEqual((receipt["state"], len(rows)), ("HASH_CHAIN_VERIFIED", 1))
        self.assertEqual(tuple(rows[0]["canonical_identity"]), identity)
        self.assertEqual((rows[0]["native_book_at"], rows[0]["native_trade_at"]),
                         (native_at, native_at))
        self.assertEqual((rows[0]["quantity_unit"], rows[0]["price_unit"]),
                         ("NO_VERIFICADO", "NO_VERIFICADO"))
        self.assertIn("CANDIDATE_GATE", quote.opening_block_reason)
        self.assertNotIn(request, observer._eligible_symbols(self.store))
        # This path has no PaperStore.add_quote() or broker instantiated:
        # native capture alone neither admits an entry nor executes a fill.
        self.assertFalse(any("PAPER_ORDER" in str(event) for event in self.store.events))

    def test_invalid_capacity_and_no_cold_slot_do_not_add_observations(self):
        with patch("rc6_dynamic_universe.promotion.capacity_controller_from_environment") as factory:
            factory.return_value.state.return_value = {"status": "BASELINE_FAIL_CLOSED"}
            symbols, _, _, _, plan = self.call_plan()
            self.assertEqual(plan.get("ppi_observe_only_requests", []), [])
            self.assertEqual(len(symbols), observer.ACTIVE_SYMBOL_LIMIT)
        small, cursor, p = observer._ppi_observe_only_replace_cold(
            self.store, self.ready[:2], {"cold": [], "warm": [],
                "pinned": [], "_a606_previous": {}},
            at=AT.isoformat(), before=0, after=2)
        self.assertEqual(small, self.ready[:2])
        self.assertEqual(cursor, 2)
        self.assertTrue(all(value == "NO_COLD_SLOT"
                            for value in p["ppi_observe_only_family_states"].values()))
        dirty, cursor, clean = observer._ppi_observe_only_replace_cold(
            self.store, self.ready[:2], {"cold": [self.ready[1]],
                "_a606_previous": {"ppi_observe_only_cycle": "INVALID"}},
            at=AT.isoformat(), before=0, after=2)
        self.assertEqual(dirty, self.ready[:2])
        self.assertEqual(cursor, 2)
        self.assertEqual(clean["ppi_observe_only_family_states"]["FCI"],
                         "CHECKPOINT_HOLD")

    def test_zero_ready_is_explicit_cold_start_blocker_not_unbudgeted_get(self):
        with self.store.connect() as c:
            c.execute("UPDATE candidate_identity_v2 SET can_simulate=0,status='PAUSED_EXPLICIT'")
        self.assertEqual(observer._eligible_symbols(self.store), ())
        symbols, total, before, after, plan = self.call_plan()
        self.assertEqual((symbols, total, before, after), ((), 0, 0, 0))
        self.assertEqual(plan["ppi_observe_only_requests"], [])
        self.assertEqual(plan["ppi_observe_only_family_states"]["FCI"],
                         "NO_COLD_SLOT")


if __name__ == "__main__":
    unittest.main()
