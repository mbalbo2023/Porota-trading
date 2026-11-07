"""A621: real SQLite joins; synthetic journal rows test *diagnosis*, never sale.

The private journal integrity verifier has its own tests. We mock its read
boundary here to exercise exact native-event association, not PPI authenticity.
"""
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from cp_contract_evidence_v2_hf6 import evidence_hash
from rc6_fi_sell_persisted_evidence import (
    _verified_compact_index, _verified_journal_index, inspect_persisted_fi_exit,
)


AT = "2026-11-03T15:00:04+00:00"
INTENT_AT = "2026-11-03T15:00:00.500000+00:00"
BOOK1_AT = "2026-11-03T15:00:00.000000+00:00"
BOOK1_KNOWN = "2026-11-03T15:00:00.200000+00:00"
BOOK2_AT = "2026-11-03T15:00:02.000000+00:00"
BOOK2_KNOWN = "2026-11-03T15:00:02.100000+00:00"
IDENTITY = ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")


class FixtureStore:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c


def native_event(*, at, known, identity=IDENTITY, seq=1):
    return {"canonical_identity": list(identity), "native_book_at": at,
            "known_at": known, "event_sha256": f"event{seq}",
            "book": {"bids": [{"position": 0, "price": "100", "quantity": "100"}],
                     "offers": [{"position": 0, "price": "101", "quantity": "100"}]},
            "quantity_unit": "NO_VERIFICADO", "price_unit": "NO_VERIFICADO"}


class PersistedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = FixtureStore(Path(self.tmp.name) / "paper.sqlite")
        self.journal = Path(self.tmp.name) / "native_journal.json"
        with self.store.connect() as db:
            db.executescript("""
            CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
            CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,source TEXT,status TEXT,
                symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,
                opened_at TEXT,closed_at TEXT,quantity TEXT,entry_price TEXT,
                target_price TEXT,stop_price TEXT,features_json TEXT);
            CREATE TABLE paper_exit_intents(paper_id TEXT PRIMARY KEY,state TEXT,cause TEXT,
                due_at TEXT,blocked_reason TEXT,supervised_at TEXT,attempts INTEGER);
            CREATE TABLE paper_fills(id INTEGER PRIMARY KEY,paper_id TEXT,source TEXT,
                side TEXT,quantity TEXT);
            CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,source TEXT,observed_at TEXT,
                symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,
                book_at TEXT,bid TEXT,ask TEXT,bid_size TEXT,ask_size TEXT);
            CREATE TABLE paper_events(id INTEGER PRIMARY KEY,event_at TEXT,source TEXT,
                event_type TEXT,paper_id TEXT,detail TEXT);
            CREATE TABLE contract_evidence_v2_snapshots(snapshot_id INTEGER PRIMARY KEY,
                evidence_hash TEXT,evidence_json TEXT,source_ref TEXT);
            CREATE TABLE contract_evidence_v2_current(family TEXT,ticker TEXT,market TEXT,
                currency TEXT,settlement TEXT,source_class TEXT,snapshot_id INTEGER,
                evidence_hash TEXT,observed_at TEXT);
            CREATE TABLE contract_evidence_v2_changes(family TEXT,ticker TEXT,market TEXT,
                currency TEXT,settlement TEXT,status TEXT);
            """)
            db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
            db.execute("""INSERT INTO paper_positions
                (paper_id,source,status,symbol,asset_class,market,currency,settlement,opened_at,
                 closed_at,quantity,entry_price,target_price,stop_price,features_json)
                 VALUES ('PAPER-FI-1','PRODUCTION_PAPER','OPEN',?,?,?,?,?,
                         '2026-11-03T14:30:00+00:00',NULL,'100','90','105','80','{}')""",
                       (IDENTITY[0], IDENTITY[1], IDENTITY[2], IDENTITY[3], IDENTITY[4]))
            db.execute("""INSERT INTO paper_exit_intents VALUES
                ('PAPER-FI-1','EXIT_DUE','EOD_PAPER',?,'',?,0)""",(INTENT_AT, INTENT_AT))
            db.execute("""INSERT INTO paper_fills VALUES
                (1,'PAPER-FI-1','PRODUCTION_PAPER','BUY_SIMULATED','100')""")
            db.execute("""INSERT INTO market_snapshots
                 (id,source,observed_at,symbol,asset_class,market,currency,settlement,
                  book_at,bid,ask,bid_size,ask_size) VALUES
                 (1,'PRODUCTION_PAPER',?,?,?,?,?,?,?,'100','101','100','100')""",
                       (BOOK1_KNOWN, *IDENTITY, BOOK1_AT))
            self.contract(db, family="BONOS")

    def contract(self, db, *, family, source="PPI_STRUCTURED_API", altered_hash=False):
        evidence = {"instrument_type": family, "source_route": "PPI:Operables"}
        digest = evidence_hash(evidence)
        db.execute("""INSERT INTO contract_evidence_v2_snapshots
            VALUES(1,?,?,?)""",("bad" if altered_hash else digest,
                                  json.dumps(evidence), "PPI:Operables"))
        db.execute("""INSERT INTO contract_evidence_v2_current
            VALUES(?,?,?,?,?,?,?,?,?)""",
                   (family, IDENTITY[0],IDENTITY[2],IDENTITY[3],IDENTITY[4],source,1,digest,
                    "2026-11-03T14:00:00+00:00"))

    def audit(self, events=None, state="HASH_CHAIN_VERIFIED", reason=""):
        events = events if events is not None else [
            native_event(at=BOOK1_AT, known=BOOK1_KNOWN),
            native_event(at=BOOK2_AT, known=BOOK2_KNOWN, seq=2)]
        with patch("rc6_fi_sell_persisted_evidence.verified_native_book_journal",
                   return_value=(events,{"state": state,"reason": reason})):
            return inspect_persisted_fi_exit(self.store,"PAPER-FI-1",at=AT,
                                            journal_path=self.journal)

    def test_persisted_join_finds_both_books_but_still_blocks_unsigned_sale(self):
        result = self.audit()
        self.assertEqual(result["status"],"BLOCKED")
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(result["real_routes_used"],[])
        self.assertEqual(result["anchors"]["book1_event_sha256"],"event1")
        self.assertEqual(result["anchors"]["provisional_book2_event_sha256"],"event2")
        self.assertEqual(result["anchors"]["ppi_contract_snapshot_ids"],[1])
        self.assertEqual(result["anchors"]["paper_modeled_settlement_date_not_broker_receipt"],
                         "2026-11-03")
        self.assertIn("FI_EXIT_DECISION_POSTCOMMIT_SIGNED_RECEIPT_MISSING",result["blockers"])
        self.assertIn("FI_EXIT_PPI_BOOK_QUANTITY_VN_UNVERIFIED",result["blockers"])
        self.assertIn("FI_EXIT_PPI_BOOK_PRICE_PER100_UNVERIFIED",result["blockers"])
        self.assertIn("FI_EXIT_ALYC_ACCOUNT_TARIFF_UNVERIFIED",result["blockers"])

    def test_book2_exact_latency_boundary_is_not_future(self):
        event = native_event(at="2026-11-03T15:00:01.500000+00:00",
                             known=BOOK2_KNOWN, seq=2)
        result = self.audit([native_event(at=BOOK1_AT,known=BOOK1_KNOWN),event])
        self.assertNotIn("provisional_book2_event_sha256", result["anchors"])
        self.assertIn("FI_EXIT_BOOK2_AFTER_PROVISIONAL_LATENCY_MISSING",result["blockers"])

    def test_special_t1_diagnostic_uses_persisted_identity_but_never_authorizes(self):
        with self.store.connect() as db:
            db.execute("UPDATE paper_positions SET settlement='A-24HS'")
        result = inspect_persisted_fi_exit(self.store, "PAPER-FI-1",
            at="2026-11-06T15:00:00+00:00", journal_path=self.journal)
        self.assertEqual(result["anchors"].get(
            "paper_modeled_settlement_date_not_broker_receipt"), "2026-11-11")
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(result["status"], "BLOCKED")

    def test_native_book2_stale_at_actual_audit_cannot_be_fresh_via_cache(self):
        with patch("rc6_fi_sell_persisted_evidence.verified_native_book_journal",
                   return_value=([native_event(at=BOOK1_AT,known=BOOK1_KNOWN),
                                  native_event(at=BOOK2_AT,known=BOOK2_KNOWN,seq=2)],
                                 {"state":"HASH_CHAIN_VERIFIED"})):
            result=inspect_persisted_fi_exit(self.store,"PAPER-FI-1",
                at="2026-11-03T15:10:00+00:00",journal_path=self.journal)
        self.assertIn("FI_EXIT_BOOK2_NATIVE_STALE_AT_AUDIT",result["blockers"])

    def test_foreign_five_key_never_relabels_a_book(self):
        foreign = ("AL30","BONOS","BYMA","USD","INMEDIATA")
        result = self.audit([native_event(at=BOOK1_AT,known=BOOK1_KNOWN,identity=foreign)])
        self.assertIn("FI_EXIT_BOOK1_SNAPSHOT_NATIVE_JOURNAL_NOT_LINKED",result["blockers"])
        self.assertEqual(result["anchors"]["ppi_native_same_identity_events"],0)

    def test_paper_snapshot_source_label_alone_is_not_ppi_proof(self):
        result = self.audit([])
        self.assertTrue(result["anchors"]["snapshot_source_is_mode_only"])
        self.assertIn("FI_EXIT_BOOK1_SNAPSHOT_NATIVE_JOURNAL_NOT_LINKED",result["blockers"])

    def test_hash_chain_reader_hold_propagates_without_buying_book(self):
        result = self.audit([],state="HOLD",reason="PPI_BOOK_JOURNAL_CHAIN_OR_IDENTITY_HOLD")
        self.assertIn("FI_EXIT_PPI_NATIVE_JOURNAL_PPI_BOOK_JOURNAL_CHAIN_OR_IDENTITY_HOLD",result["blockers"])

    def test_missing_contract_current_cannot_use_primary_name_as_contract(self):
        with self.store.connect() as c:
            c.execute("DELETE FROM contract_evidence_v2_current")
        result=self.audit()
        self.assertIn("FI_EXIT_PPI_FIVE_KEY_CONTRACT_NOT_VERIFIED",result["blockers"])

    def test_non_ppi_contract_not_promoted_to_primary(self):
        with self.store.connect() as c:
            c.execute("UPDATE contract_evidence_v2_current SET source_class='IOL_STRUCTURED_API'")
        result=self.audit()
        self.assertIn("FI_EXIT_PPI_FIVE_KEY_CONTRACT_NOT_VERIFIED",result["blockers"])

    def test_corrupt_contract_hash_is_blocking(self):
        with self.store.connect() as c:
            c.execute("UPDATE contract_evidence_v2_snapshots SET evidence_hash='tampered'")
        result=self.audit()
        self.assertIn("FI_EXIT_CONTRACT_V2_HASH_OR_PIT_INVALID",result["blockers"])

    def test_duplicate_json_key_is_rejected_even_with_recomputed_hash(self):
        with self.store.connect() as c:
            c.execute("UPDATE contract_evidence_v2_snapshots SET evidence_json=?",
                      ('{"instrument_type":"BONOS","instrument_type":"BONOS"}',))
        result=self.audit()
        self.assertIn("FI_EXIT_CONTRACT_V2_HASH_OR_PIT_INVALID",result["blockers"])

    def test_buy_fill_quantity_mismatch_blocks_nominal_lot(self):
        with self.store.connect() as c:
            c.execute("UPDATE paper_fills SET quantity='200'")
        result=self.audit()
        self.assertIn("FI_EXIT_IMMUTABLE_BUY_PAPER_LEDGER_INVALID",result["blockers"])

    def test_legacy_sell_fill_without_native_receipt_blocks_reconciliation(self):
        with self.store.connect() as c:
            c.execute("""INSERT INTO paper_fills VALUES
                (2,'PAPER-FI-1','PRODUCTION_PAPER','SELL_SIMULATED','10')""")
        result=self.audit()
        self.assertIn("FI_EXIT_NATIVE_SALE_LEDGER_MISSING_WITH_SELL_FILLS",result["blockers"])

    def test_open_position_with_prior_sell_and_native_receipt_still_needs_review(self):
        with self.store.connect() as c:
            c.execute("""CREATE TABLE paper_fi_native_sales(paper_id TEXT)""")
            c.execute("""INSERT INTO paper_fills VALUES
                (2,'PAPER-FI-1','PRODUCTION_PAPER','SELL_SIMULATED','10')""")
            c.execute("INSERT INTO paper_fi_native_sales VALUES('PAPER-FI-1')")
        result=self.audit()
        self.assertIn("FI_EXIT_PRIOR_SALES_REQUIRE_INDEPENDENT_LEDGER_RECONCILIATION",result["blockers"])
        self.assertEqual(result["anchors"]["prior_paper_sell_fill_count"],1)
        self.assertEqual(result["anchors"]["prior_native_sale_receipt_count"],1)
        self.assertFalse(result["execution_authorized"])

    def test_index_reuses_same_unchanged_verified_file_without_full_8mb_parse_per_position(self):
        # Fixture bytes are only for testing the *index* caching boundary; the
        # mocked verifier does not claim these bytes are an authentic PPI book.
        self.journal.write_bytes(b"synthetic-fi-cache-v1")
        self.journal.chmod(0o600)
        rows=[native_event(at=BOOK1_AT,known=BOOK1_KNOWN),
              native_event(at=BOOK2_AT,known=BOOK2_KNOWN,seq=2)]
        with self.store.connect() as c:
            c.execute("""INSERT INTO paper_positions
              (paper_id,source,status,symbol,asset_class,market,currency,settlement,
               opened_at,closed_at,quantity,entry_price,target_price,stop_price,features_json)
              SELECT 'PAPER-FI-2',source,status,symbol,asset_class,market,currency,
                     settlement,opened_at,closed_at,quantity,entry_price,target_price,
                     stop_price,features_json FROM paper_positions WHERE paper_id='PAPER-FI-1'""")
            c.execute("""INSERT INTO paper_exit_intents
              SELECT 'PAPER-FI-2',state,cause,due_at,blocked_reason,supervised_at,attempts
              FROM paper_exit_intents WHERE paper_id='PAPER-FI-1'""")
            c.execute("""INSERT INTO paper_fills
              VALUES(2,'PAPER-FI-2','PRODUCTION_PAPER','BUY_SIMULATED','100')""")
        _verified_compact_index.cache_clear()
        with patch("rc6_fi_sell_persisted_evidence.verified_native_book_journal",
                   return_value=(rows,{"state":"HASH_CHAIN_VERIFIED"})) as verified:
            first=inspect_persisted_fi_exit(self.store,"PAPER-FI-1",at=AT,journal_path=self.journal)
            second=inspect_persisted_fi_exit(self.store,"PAPER-FI-2",at=AT,journal_path=self.journal)
            self.assertEqual(verified.call_count,1)
            self.assertEqual(_verified_compact_index.cache_info().currsize,1)
            self.assertEqual(first["anchors"]["book1_event_sha256"],"event1")
            self.assertEqual(second["anchors"]["book1_event_sha256"],"event1")
            self.assertFalse(second["execution_authorized"])
            # In-place changed bytes change ctime/mtime/size, forcing a fresh
            # canonical verifier despite an otherwise identical path.
            self.journal.write_bytes(b"synthetic-fi-cache-v2-modified")
            third=inspect_persisted_fi_exit(self.store,"PAPER-FI-1",at=AT,journal_path=self.journal)
            self.assertEqual(verified.call_count,2)
            self.assertFalse(third["execution_authorized"])

    def test_cached_projection_cannot_retain_unneeded_full_native_depth(self):
        self.journal.write_bytes(b"synthetic-dense-book")
        self.journal.chmod(0o600)
        event=native_event(at=BOOK1_AT,known=BOOK1_KNOWN)
        event["book"]["bids"].extend({"position":n,"price":"99", "quantity":"1"}
                                      for n in range(1,64))
        _verified_compact_index.cache_clear()
        with patch("rc6_fi_sell_persisted_evidence.verified_native_book_journal",
                   return_value=([event],{"state":"HASH_CHAIN_VERIFIED"})):
            indexed,receipt=_verified_journal_index(self.journal)
        self.assertEqual(receipt["state"],"HASH_CHAIN_VERIFIED")
        self.assertEqual(len(indexed),1)
        self.assertEqual(len(indexed[0]["book"]["bids"]),1)
        self.assertLessEqual(_verified_compact_index.cache_info().currsize,1)

    def test_zero_bid_book2_cannot_be_executable(self):
        future=native_event(at=BOOK2_AT,known=BOOK2_KNOWN,seq=2)
        future["book"]["bids"][0]["price"]="0"
        result=self.audit([native_event(at=BOOK1_AT,known=BOOK1_KNOWN),future])
        self.assertIn("FI_EXIT_BOOK2_NATIVE_BID_OR_DEPTH_NOT_EXECUTABLE",result["blockers"])

    def test_material_contract_change_stops_sale(self):
        with self.store.connect() as c:
            c.execute("INSERT INTO contract_evidence_v2_changes VALUES(?,?,?,?,?,?)",
                      ("BONOS","AL30","BYMA","ARS","INMEDIATA","CHANGED_REVIEW_REQUIRED"))
        result=self.audit()
        self.assertIn("FI_EXIT_CONTRACT_MATERIAL_CHANGE_REVIEW_PENDING",result["blockers"])

    def test_real_order_counter_nonzero_is_hold(self):
        with self.store.connect() as c:
            c.execute("UPDATE observer_state SET real_orders_sent=1")
        result=self.audit()
        self.assertIn("FI_EXIT_PAPER_ZERO_REAL_ORDERS_REQUIRED",result["blockers"])

    def test_open_intent_without_exit_cause_not_credited(self):
        with self.store.connect() as c:
            c.execute("UPDATE paper_exit_intents SET state='OPEN',cause=NULL")
        result=self.audit()
        self.assertIn("FI_EXIT_INTENT_NOT_DUE",result["blockers"])

    def test_missing_journal_never_infers_ppi_from_snapshot(self):
        result = self.audit([],state="NO_VERIFICADO",reason="PPI_BOOK_JOURNAL_ABSENT")
        self.assertIn("FI_EXIT_PPI_NATIVE_JOURNAL_PPI_BOOK_JOURNAL_ABSENT",result["blockers"])

    def test_no_broker_api_or_write_side_effects(self):
        with self.store.connect() as c:
            before=c.execute("SELECT COUNT(*) FROM paper_events").fetchone()[0]
        result=self.audit()
        with self.store.connect() as c:
            after=c.execute("SELECT COUNT(*) FROM paper_events").fetchone()[0]
        self.assertEqual((before,after),(0,0))
        self.assertFalse(result["book2_is_execution_authority"])

    def test_wrong_family_fails_closed(self):
        with self.store.connect() as c:
            c.execute("UPDATE paper_positions SET asset_class='ACCIONES'")
        result=self.audit()
        self.assertFalse(result["execution_authorized"])
        self.assertIn("FI_EXIT_DB_EVIDENCE_INVALID_OR_UNREADABLE",result["blockers"])

    def test_invalid_latency_fails_closed_before_io(self):
        result=inspect_persisted_fi_exit(self.store,"PAPER-FI-1",at=AT,
                                         journal_path=self.journal,latency_seconds="NaN")
        self.assertIn("FI_EXIT_AUDIT_CLOCK_OR_LATENCY_INVALID",result["blockers"])


if __name__ == "__main__":
    unittest.main()
