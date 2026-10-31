"""Synthetic FI terminal-review proofs, never issuer credentials or live PAPER authority.

``unittest`` allows bounded offline execution even when pytest is unavailable;
pytest also collects these same tests in the governed suite.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from be_paper_engine import PaperStore
from bs_instrument_contracts import contract_from_metadata
from cf_sale_settlement import conservative_unconfirmed_availability
from rc6_fixed_income_event_ledger import apply_verified_receipt
from rc6_fixed_income_native_redemption import (
    ENTRY_AUTHORITY, NATIVE_EXIT_AUTHORITY, PAPER_ONLY, REAL_ROUTES_USED,
    REVIEW_AUTHORITY, REVIEW_SCHEMA, apply_verified_redemption_review,
    init_schema,
)
from rc6_fixed_income_portfolio_bridge import cash_effect_checked, position_at


OPENED = "2026-10-27T14:00:00+00:00"
PAY = "2026-10-29T15:00:00+00:00"
KNOWN = "2026-10-29T15:06:00+00:00"
BOOKED = "2026-10-29T16:00:00+00:00"
ISSUER = b"SYNTHETIC issuer final maturity terms, not authentic evidence"
PAYMENT = b"SYNTHETIC final settlement and rights, not authentic evidence"


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _hash(value):
    return hashlib.sha256(value).hexdigest()


class NativeRedemptionReviewTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc6-fi-redemption-")
        self.addCleanup(temporary.cleanup)
        self.store = PaperStore(str(Path(temporary.name) / "fi.db"))
        self.family = "BONOS"
        self._make_paper_lot()
        init_schema(self.store)  # migration owner, never runtime hot-path DDL

    def _make_paper_lot(self):
        contract = contract_from_metadata("AL30", self.family, {
            "market": "BYMA", "currency": "ARS", "settlement": "A-24HS",
            "cash_multiplier": "0.01", "quantity_step": "1", "minimum_quantity": "1",
            "metadata_source": "SYNTHETIC_EXACT_PAPER_CONTRACT",
        })
        features = {"financial_contract": asdict(contract),
                    "contract_cash_multiplier": "0.01"}
        with self.store.connect() as db:
            db.execute("""INSERT INTO paper_positions
               (paper_id,source,strategy_version,symbol,asset_class,settlement,status,
                quantity,entry_price,entry_cost,stop_price,target_price,opened_at,
                features_json,currency,market,currency_source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               ("PAPER-AL30", "PRODUCTION_PAPER", "SYNTHETIC_NO_ENTRY_AUTHORITY",
                "AL30", self.family, "A-24HS", "OPEN", "100", "100", "0.61",
                "90", "110", OPENED, json.dumps(features, default=str),
                "ARS", "BYMA", "SYNTHETIC"))
            db.execute("""INSERT INTO paper_fills
               (paper_id,source,side,filled_at,quantity,price,costs,slippage)
               VALUES(?,?,?,?,?,?,?,?)""",
               ("PAPER-AL30", "PRODUCTION_PAPER", "BUY_SIMULATED",
                OPENED, "100", "100", "0.61", "0"))

    def _set_family(self, family):
        self.family = family
        contract = contract_from_metadata("AL30", family, {
            "market": "BYMA", "currency": "ARS", "settlement": "A-24HS",
            "cash_multiplier": "0.01", "quantity_step": "1", "minimum_quantity": "1",
            "metadata_source": "SYNTHETIC_EXACT_PAPER_CONTRACT",
        })
        with self.store.connect() as db:
            row = db.execute("SELECT features_json FROM paper_positions").fetchone()
            features = json.loads(row[0])
            features["financial_contract"] = asdict(contract)
            db.execute("UPDATE paper_positions SET asset_class=?, features_json=?",
                       (family, json.dumps(features, default=str)))

    def _economic_receipt(self, *, redeemed="100", event_id="maturity"):
        settlement = conservative_unconfirmed_availability("A-24HS", OPENED).isoformat()
        return {
            "schema": "rc6.fixed-income-economic-receipt.v1",
            "paper_only": True, "real_routes_used": [],
            "review_authority": "RELEASE_REVIEWED_ECONOMIC_EVENT",
            "identity": ["AL30", self.family, "BYMA", "ARS", "A-24HS"],
            "event": {
                "event_id": event_id, "obligation_id": event_id + "-obligation",
                "paper_id": "PAPER-AL30", "kind": "AMORTIZATION",
                "effective_at": PAY, "recorded_at": KNOWN,
                "source_at": KNOWN, "source_kind": "SETTLEMENT_PROOF",
                "source_reference": "SYNTHETIC_PAYMENT_NOTICE", "source_sha256": _hash(PAYMENT),
                "payment_currency": "ARS", "cash_amount": redeemed,
                "nominal_base": "100", "nominal_redeemed": redeemed,
            },
            "cash_per_nominal": "1",
            "entitlement": {
                "rule": "TRADE_BEFORE_EX_AND_SETTLED_BY_RECORD",
                "source_sha256": _hash(PAYMENT),
                "source_reference": "SYNTHETIC_PAYMENT_NOTICE",
                "ex_at": "2026-10-29T12:00:00+00:00",
                "record_at": "2026-10-29T13:00:00+00:00",
                "paper_settled_at": settlement,
                "settlement_basis": "PAPER_CONSERVATIVE_FULL_SETTLEMENT_DATE",
                "known_at": KNOWN,
            },
        }

    def _book(self, *, redeemed="100", economic=None, at=BOOKED):
        economic = economic if economic is not None else self._economic_receipt(redeemed=redeemed)
        raw = _raw(economic)
        apply_verified_receipt(
            self.store, receipt_bytes=raw, approved_sha256=_hash(raw),
            source_bytes=PAYMENT, applied_at=at)

    def _review(self):
        return {
            "schema": REVIEW_SCHEMA, "paper_only": True, "real_routes_used": [],
            "review_authority": REVIEW_AUTHORITY,
            "all_maturity_obligations_reviewed": True,
            "paper_id": "PAPER-AL30", "identity": ["AL30", self.family, "BYMA", "ARS", "A-24HS"],
            "final_event_id": "maturity", "paid_obligation_ids": ["maturity-obligation"],
            "final_principal_cash": "100", "final_nominal_redeemed": "100",
            "issuer_terms": {
                "source_sha256": _hash(ISSUER),
                "source_reference": "SYNTHETIC_REVIEWED_ISSUER_SCHEDULE",
                "source_at": "2026-10-28T12:00:00+00:00",
                "known_at": "2026-10-28T13:00:00+00:00",
                "maturity_at": PAY, "principal_currency": "ARS",
                "redemption_basis": "FIXED_KNOWN_FACE", "indexation": "NONE",
                "redemption_face_official": True,
            },
        }

    def _apply(self, data=None, *, issuer_bytes=ISSUER, approved_sha256=None, at=BOOKED):
        raw = _raw(data if data is not None else self._review())
        return apply_verified_redemption_review(
            self.store, receipt_bytes=raw,
            approved_sha256=approved_sha256 or _hash(raw),
            issuer_bytes=issuer_bytes, applied_at=at)

    def _review_count(self):
        with self.store.connect() as db:
            return db.execute("SELECT count(*) FROM paper_fi_native_redemption_reviews").fetchone()[0]

    def test_bond_full_redemption_review_is_idempotent_and_never_pays_or_closes(self):
        self._book()
        with self.store.connect() as db:
            before_cash = cash_effect_checked(db, "ARS", BOOKED)
            before_view = position_at(db, "PAPER-AL30", BOOKED)
        first = self._apply()
        self.assertEqual(self._apply(at="2026-10-30T16:00:00+00:00"), first)
        self.assertEqual(self._review_count(), 1)
        self.assertEqual(first["principal_by_currency"], [["ARS", "100"]])
        self.assertEqual(first["cash_side_effect"], "0")
        self.assertFalse(first["native_exit_authorized"])
        with self.store.connect() as db:
            self.assertEqual(cash_effect_checked(db, "ARS", BOOKED), before_cash)
            self.assertEqual(position_at(db, "PAPER-AL30", BOOKED), before_view)
            self.assertEqual(db.execute("SELECT status FROM paper_positions").fetchone()[0], "OPEN")
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fills").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0], 0)
        self.assertTrue(PAPER_ONLY)
        self.assertFalse(ENTRY_AUTHORITY or NATIVE_EXIT_AUTHORITY or REAL_ROUTES_USED)

    def test_letter_review_requires_exact_fixed_face_not_issuer_inference(self):
        self._set_family("LETRAS")
        self._book()
        self.assertEqual(self._apply()["family"], "LETRAS")
        bad = self._review()
        bad["issuer_terms"]["indexation"] = "CER"
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_LETTER_FACE_UNVERIFIED"):
            self._apply(bad)
        self.assertEqual(self._review_count(), 1)

    def test_obligaciones_review_is_read_only_and_currency_explicit(self):
        self._set_family("OBLIGACIONES")
        self._book()
        result = self._apply()
        self.assertEqual(result["family"], "OBLIGACIONES")
        self.assertEqual(result["principal_by_currency"], [["ARS", "100"]])
        self.assertFalse(result["native_exit_authorized"])
        with self.store.connect() as db:
            self.assertEqual(position_at(db, "PAPER-AL30", BOOKED).residual_nominal, 0)
            self.assertEqual(cash_effect_checked(db, "ARS", BOOKED), Decimal(100))
            self.assertEqual(db.execute("SELECT status FROM paper_positions").fetchone()[0], "OPEN")

    def test_partial_amortization_then_exact_final_principal_pays_once(self):
        self._book(redeemed="30")
        final = self._economic_receipt(redeemed="70", event_id="final")
        final["event"].update(nominal_base="70", effective_at="2026-11-03T15:00:00+00:00",
                              recorded_at="2026-11-03T15:06:00+00:00",
                              source_at="2026-11-03T15:06:00+00:00")
        final["entitlement"].update(ex_at="2026-11-03T12:00:00+00:00",
                                    record_at="2026-11-03T13:00:00+00:00",
                                    known_at="2026-11-03T15:06:00+00:00")
        later = "2026-11-03T16:00:00+00:00"
        self._book(economic=final, at=later)
        reviewed = self._review()
        reviewed.update(final_event_id="final", final_principal_cash="70",
                        final_nominal_redeemed="70",
                        paid_obligation_ids=["maturity-obligation", "final-obligation"])
        reviewed["issuer_terms"]["maturity_at"] = final["event"]["effective_at"]
        result = self._apply(reviewed, at=later)
        self.assertEqual(result["residual_nominal"], "0")
        self.assertEqual(result["principal_by_currency"], [["ARS", "100"]])
        self.assertEqual(self._apply(reviewed, at=later), result)
        with self.store.connect() as db:
            self.assertEqual(position_at(db, "PAPER-AL30", later).event_count, 2)
            self.assertEqual(cash_effect_checked(db, "ARS", later), Decimal(100))
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT status FROM paper_positions").fetchone()[0], "OPEN")

    def test_partial_redeemed_nominal_cannot_be_terminal_review(self):
        self._book(redeemed="30")
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_NOT_FULLY_AMORTIZED"):
            self._apply()
        self.assertEqual(self._review_count(), 0)

    def test_no_fi_event_is_not_an_inferred_maturity(self):
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_NOT_FULLY_AMORTIZED"):
            self._apply()
        self.assertEqual(self._review_count(), 0)

    def test_external_approval_hash_and_issuer_bytes_are_independent_guards(self):
        self._book()
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_RECEIPT_PIN_MISMATCH"):
            self._apply(approved_sha256="a" * 64)
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_ISSUER_SOURCE_MISMATCH"):
            self._apply(issuer_bytes=b"different issuer document")
        self.assertEqual(self._review_count(), 0)

    def test_missing_complete_obligation_coverage_does_not_close(self):
        self._book()
        bad = self._review()
        bad["paid_obligation_ids"] = ["some-other-obligation"]
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_OBLIGATION_COVERAGE_MISMATCH"):
            self._apply(bad)
        self.assertEqual(self._review_count(), 0)

    def test_malformed_obligations_fail_as_review_error(self):
        self._book()
        bad = self._review()
        bad["paid_obligation_ids"] = [{"untrusted": "object"}]
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_OBLIGATIONS_INVALID"):
            self._apply(bad)
        self.assertEqual(self._review_count(), 0)

    def test_terminal_event_and_amount_must_match_booked_settlement(self):
        self._book()
        for mutate, expected in (
            (lambda d: d.update(final_event_id="no-such-event"), "TERMINAL_EVENT_MISMATCH"),
            (lambda d: d.update(final_nominal_redeemed="99"), "TERMINAL_ECONOMICS_MISMATCH"),
            (lambda d: d.update(final_principal_cash="101"), "TERMINAL_ECONOMICS_MISMATCH"),
            (lambda d: d["issuer_terms"].update(maturity_at="2026-10-30T15:00:00+00:00"),
             "TERMINAL_EVENT_MISMATCH"),
        ):
            bad = self._review()
            mutate(bad)
            with self.assertRaisesRegex(ValueError, expected):
                self._apply(bad, at="2026-10-30T16:00:00+00:00")
        self.assertEqual(self._review_count(), 0)

    def test_paper_safety_and_issuer_future_knowledge_fail_closed(self):
        self._book()
        with self.store.connect() as db:
            db.execute("UPDATE observer_state SET real_orders_sent=1")
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_PAPER_SAFETY_REQUIRED"):
            self._apply()
        with self.store.connect() as db:
            db.execute("UPDATE observer_state SET real_orders_sent=0")
        bad = self._review()
        bad["issuer_terms"]["known_at"] = "2026-11-01T12:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_ISSUER_CLOCK_INVALID"):
            self._apply(bad)
        self.assertEqual(self._review_count(), 0)

    def test_corrupt_ledger_cash_cannot_pretend_maturity(self):
        self._book()
        with self.store.connect() as db:
            db.execute("UPDATE paper_fi_cash_entries SET amount='999'")
        with self.assertRaisesRegex(ValueError, "FI_PORTFOLIO_EVENT_CASH_CONFLICT"):
            self._apply()
        self.assertEqual(self._review_count(), 0)

    def test_changed_receipt_or_ledger_after_review_fails_replay(self):
        self._book()
        self._apply()
        changed = self._review()
        changed["issuer_terms"]["source_reference"] = "DIFFERENT_REVIEWED_ISSUER_REFERENCE"
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_REVIEW_REPLAY_CONFLICT"):
            self._apply(changed)
        with self.store.connect() as db:
            db.execute("UPDATE paper_fi_economic_events SET receipt_sha256=?",
                       ("b" * 64,))
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_REVIEW_REPLAY_CONFLICT"):
            self._apply()
        self.assertEqual(self._review_count(), 1)

    def test_schema_absent_fails_closed_without_hotpath_ddl(self):
        self._book()
        with self.store.connect() as db:
            db.execute("DROP TABLE paper_fi_native_redemption_reviews")
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_REVIEW_SCHEMA_MISSING"):
            self._apply()

    def test_insert_failure_rolls_back_without_touching_cash(self):
        self._book()
        with self.store.connect() as db:
            before = cash_effect_checked(db, "ARS", BOOKED)
            db.execute("""CREATE TRIGGER synthetic_review_failure
                BEFORE INSERT ON paper_fi_native_redemption_reviews
                BEGIN SELECT RAISE(ABORT,'synthetic review failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic review failure"):
            self._apply()
        with self.store.connect() as db:
            self.assertEqual(cash_effect_checked(db, "ARS", BOOKED), before)
        self.assertEqual(self._review_count(), 0)

    def test_concurrent_exact_review_replay_is_singleton(self):
        self._book()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self._apply(), range(4)))
        self.assertEqual(results, [results[0]] * 4)
        self.assertEqual(self._review_count(), 1)


if __name__ == "__main__":
    unittest.main()
