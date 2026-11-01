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
    REVIEW_AUTHORITY, REVIEW_SCHEMA, SCHEDULE_SCHEMA, apply_verified_redemption_review,
    init_schema, normalized_issuer_schedule_sha256,
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
        payment = self._economic_receipt()["event"]
        return {
            "schema": REVIEW_SCHEMA, "paper_only": True, "real_routes_used": [],
            "review_authority": REVIEW_AUTHORITY,
            "all_maturity_obligations_reviewed": True,
            "paper_id": "PAPER-AL30", "identity": ["AL30", self.family, "BYMA", "ARS", "A-24HS"],
            "final_event_id": "maturity", "paid_obligation_ids": ["maturity-obligation"],
            "final_principal_cash": "100", "final_nominal_redeemed": "100",
            "expected_principal_by_currency": [["ARS", "100"]],
            "expected_cash_by_currency": [["ARS", "100"]],
            "issuer_schedule": {
                "schema": SCHEDULE_SCHEMA, "review_authority": "INDEPENDENT_ISSUER_SCHEDULE_REVIEWED",
                "identity": ["AL30", self.family, "BYMA", "ARS", "A-24HS"],
                "issuer_id": "SYNTHETIC_ISSUER_NOT_VERIFIED",
                "series_id": "SYNTHETIC_SERIES_NOT_VERIFIED",
                "instrument_class": {"BONOS": "BOND", "LETRAS": "BILL",
                                     "OBLIGACIONES": "CORPORATE_ON"}[self.family],
                "bopreal_classification": "NOT_BOPREAL_INDEPENDENTLY_REVIEWED",
                "source_sha256": _hash(ISSUER),
                "source_reference": "SYNTHETIC_REVIEWED_ISSUER_SCHEDULE",
                "source_at": "2026-10-28T12:00:00+00:00",
                "known_at": "2026-10-28T13:00:00+00:00",
                "maturity_at": PAY, "principal_currency": "ARS",
                "redemption_basis": "FIXED_KNOWN_FACE", "indexation": "NONE",
                "redemption_face_official": True,
                "final_obligation_id": "maturity-obligation",
                "obligations": [{key: payment[key] for key in (
                    "obligation_id", "kind", "effective_at", "payment_currency",
                    "cash_amount", "nominal_base", "nominal_redeemed")}],
            },
        }

    def _apply(self, data=None, *, issuer_bytes=ISSUER, approved_sha256=None,
               approved_issuer_sha256=None, approved_schedule_sha256=None, at=BOOKED):
        data = data if data is not None else self._review()
        raw = _raw(data)
        schedule = data.get("issuer_schedule")
        return apply_verified_redemption_review(
            self.store, receipt_bytes=raw,
            approved_sha256=approved_sha256 or _hash(raw),
            approved_issuer_sha256=approved_issuer_sha256 or _hash(ISSUER),
            approved_schedule_sha256=approved_schedule_sha256 or
                normalized_issuer_schedule_sha256(schedule),
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
        self.assertEqual(first["cash_by_currency"], [["ARS", "100"]])
        self.assertEqual(first["reconciled_cash_event_count"], 1)
        self.assertEqual(first["issuer_id"], "SYNTHETIC_ISSUER_NOT_VERIFIED")
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
        bad["issuer_schedule"]["indexation"] = "CER"
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

    def test_unicode_issuer_series_has_stable_canonical_schedule_pin(self):
        self._book()
        reviewed = self._review()
        reviewed["issuer_schedule"]["issuer_id"] = "EMISOR Nº 42 — SIMULADO"
        expected_pin = normalized_issuer_schedule_sha256(reviewed["issuer_schedule"])
        self.assertEqual(len(expected_pin), 64)
        self.assertEqual(self._apply(reviewed)["issuer_schedule_sha256"], expected_pin)

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
        reviewed["issuer_schedule"]["maturity_at"] = final["event"]["effective_at"]
        reviewed["issuer_schedule"]["final_obligation_id"] = "final-obligation"
        reviewed["issuer_schedule"]["obligations"][0]["cash_amount"] = "30"
        reviewed["issuer_schedule"]["obligations"][0]["nominal_redeemed"] = "30"
        reviewed["issuer_schedule"]["obligations"].append({key: final["event"][key] for key in (
            "obligation_id", "kind", "effective_at", "payment_currency",
            "cash_amount", "nominal_base", "nominal_redeemed")})
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

    def test_zero_cash_amortization_cannot_be_mislabeled_paid_final_redemption(self):
        # SIMULATED zero-settlement event may reduce nominal in the ledger;
        # that is not evidence of an actually paid terminal redemption.
        unpaid = self._economic_receipt()
        unpaid["event"]["cash_amount"] = "0"
        unpaid["cash_per_nominal"] = "0"
        self._book(economic=unpaid)
        receipt = self._review()
        receipt["final_principal_cash"] = "0"
        receipt["expected_principal_by_currency"] = [["ARS", "0"]]
        receipt["expected_cash_by_currency"] = [["ARS", "0"]]
        receipt["issuer_schedule"]["obligations"][0]["cash_amount"] = "0"
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_TERMINAL_ECONOMICS_MISMATCH"):
            self._apply(receipt)
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
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_ISSUER_SOURCE_MISMATCH"):
            self._apply(approved_issuer_sha256="a" * 64)
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_ISSUER_SCHEDULE_PIN_MISMATCH"):
            self._apply(approved_schedule_sha256="a" * 64)
        self.assertEqual(self._review_count(), 0)

    def test_issuer_series_identity_and_literal_bopreal_policy_fail_closed(self):
        self._book()
        mutations = (
            (lambda s: s["identity"].__setitem__(0, "OTHER_BOND"),
             "ISSUER_SCHEDULE_IDENTITY_MISMATCH"),
            (lambda s: s["identity"].__setitem__(3, "USD"),
             "ISSUER_SCHEDULE_IDENTITY_MISMATCH"),
            (lambda s: s.update(issuer_id=""), "ISSUER_ID_INVALID"),
            (lambda s: s.update(series_id=""), "ISSUER_SERIES_INVALID"),
            (lambda s: s.update(instrument_class="CORPORATE_ON"),
             "ISSUER_INSTRUMENT_CLASS_MISMATCH"),
            (lambda s: s.update(instrument_class="BOPREAL"),
             "BOPREAL_POLICY_UNVERIFIED"),
            (lambda s: s.update(series_id="SYNTHETIC_BOPREAL_SERIES"),
             "BOPREAL_POLICY_UNVERIFIED"),
            (lambda s: s.update(issuer_id="BOPREAL_ISSUER_HINT"),
             "BOPREAL_POLICY_UNVERIFIED"),
            (lambda s: s.update(indexation="BOPREAL_UNVERIFIED_FX"),
             "BOPREAL_POLICY_UNVERIFIED"),
            (lambda s: s.update(bopreal_classification="UNVERIFIED"),
             "BOPREAL_POLICY_UNVERIFIED"),
            (lambda s: s.pop("bopreal_classification"),
             "ISSUER_SCHEDULE_FIELDS_INVALID"),
            (lambda s: s.update(source_sha256="b" * 64),
             "ISSUER_SOURCE_OR_REVIEW_UNVERIFIED"),
            (lambda s: s.update(review_authority="CLAIMED_BY_PROVIDER"),
             "ISSUER_SOURCE_OR_REVIEW_UNVERIFIED"),
            (lambda s: s.update(source_at="2026-11-01T16:00:00+00:00"),
             "ISSUER_CLOCK_INVALID"),
        )
        for mutate, blocker in mutations:
            with self.subTest(blocker=blocker, mutate=str(mutate)):
                bad = self._review()
                mutate(bad["issuer_schedule"])
                with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_" + blocker):
                    self._apply(bad)
        self.assertEqual(self._review_count(), 0)

    def test_malformed_receipt_identity_is_stable_blocker_not_typeerror(self):
        self._book()
        for identity in ({"AL30": "BONOS"}, ["AL30", {}, "BYMA", "ARS", "A-24HS"],
                         ["AL30", "BONOS"], None):
            with self.subTest(identity=identity):
                receipt = self._review()
                receipt["identity"] = identity
                with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_IDENTITY_MISMATCH"):
                    self._apply(receipt)
        self.assertEqual(self._review_count(), 0)

    def test_issuer_schedule_exact_event_and_cash_coverage_negative_rca(self):
        self._book()
        mutations = (
            (lambda d: d["issuer_schedule"]["obligations"][0].update(
                obligation_id="UNBOOKED"), "ISSUER_SCHEDULE_COVERAGE_MISMATCH"),
            (lambda d: d["issuer_schedule"]["obligations"][0].update(
                cash_amount="101"), "ISSUER_SCHEDULE_ECONOMICS_MISMATCH"),
            (lambda d: d["issuer_schedule"]["obligations"][0].update(
                nominal_base="99"), "ISSUER_SCHEDULE_REDEMPTION_INVALID"),
            (lambda d: d["issuer_schedule"]["obligations"][0].update(
                nominal_redeemed="99"), "ISSUER_SCHEDULE_ECONOMICS_MISMATCH"),
            (lambda d: d["issuer_schedule"]["obligations"][0].update(
                kind="COUPON", nominal_redeemed=None),
             "ISSUER_SCHEDULE_ECONOMICS_MISMATCH"),
            (lambda d: d["issuer_schedule"]["obligations"][0].update(
                payment_currency="USD"), "ISSUER_PRINCIPAL_CURRENCY_MISMATCH"),
            (lambda d: d["issuer_schedule"]["obligations"][0].update(
                effective_at="2026-10-29T14:00:00+00:00"),
             "ISSUER_SCHEDULE_ECONOMICS_MISMATCH"),
            (lambda d: d["issuer_schedule"]["obligations"].append(
                dict(d["issuer_schedule"]["obligations"][0])),
             "ISSUER_SCHEDULE_DUPLICATE_OBLIGATION"),
            (lambda d: d["issuer_schedule"]["obligations"].clear(),
             "ISSUER_SCHEDULE_OBLIGATIONS_INVALID"),
            (lambda d: d["issuer_schedule"].update(final_obligation_id="WRONG"),
             "TERMINAL_EVENT_MISMATCH"),
            (lambda d: d.update(expected_principal_by_currency=[["ARS", "101"]]),
             "LEDGER_CASH_TOTALS_MISMATCH"),
            (lambda d: d.update(expected_cash_by_currency=[["USD", "100"]]),
             "LEDGER_CASH_TOTALS_MISMATCH"),
            (lambda d: d.update(expected_cash_by_currency=[["ARS", "100"], ["ARS", "0"]]),
             "CASH_TOTALS_DUPLICATE_CURRENCY"),
            (lambda d: d.update(native_exit_authorized=True),
             "REVIEW_FIELDS_INVALID"),
            (lambda d: d["issuer_schedule"].update(paper_cash_post_authorized=True),
             "ISSUER_SCHEDULE_FIELDS_INVALID"),
        )
        for mutate, blocker in mutations:
            with self.subTest(blocker=blocker, mutate=str(mutate)):
                bad = self._review()
                mutate(bad)
                with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_" + blocker):
                    self._apply(bad)
        self.assertEqual(self._review_count(), 0)

    def test_explicit_cross_currency_final_cash_is_not_converted(self):
        economic = self._economic_receipt()
        economic["event"]["payment_currency"] = "USD"
        self._book(economic=economic)
        receipt = self._review()
        schedule = receipt["issuer_schedule"]
        schedule["principal_currency"] = "USD"
        schedule["obligations"][0]["payment_currency"] = "USD"
        receipt["expected_principal_by_currency"] = [["USD", "100"]]
        receipt["expected_cash_by_currency"] = [["USD", "100"]]
        result = self._apply(receipt)
        self.assertEqual(result["principal_by_currency"], [["USD", "100"]])
        self.assertEqual(result["cash_by_currency"], [["USD", "100"]])
        with self.store.connect() as db:
            self.assertEqual(cash_effect_checked(db, "ARS", BOOKED), Decimal(0))
            self.assertEqual(cash_effect_checked(db, "USD", BOOKED), Decimal(100))
        self.assertFalse(result["native_exit_authorized"])

    def test_simulated_coupon_cash_only_on_booking_then_final_principal_once(self):
        # Coupon devengo is NOT presumed a cash credit. Historical + future
        # synthetic receipts never establish a live issuer right.
        coupon = self._economic_receipt(event_id="coupon")
        coupon["event"].update(kind="COUPON", cash_amount="10", nominal_redeemed=None)
        coupon["cash_per_nominal"] = "0.1"
        with self.store.connect() as db:
            self.assertEqual(cash_effect_checked(db, "ARS", OPENED), Decimal(0))
        self._book(economic=coupon)
        with self.store.connect() as db:
            before_maturity = position_at(db, "PAPER-AL30", BOOKED)
            self.assertEqual(before_maturity.residual_nominal, Decimal(100))
            self.assertEqual(before_maturity.coupon_by_currency, (("ARS", Decimal(10)),))
            self.assertEqual(before_maturity.principal_by_currency, ())

        maturity_at = "2026-11-03T15:00:00+00:00"
        maturity_known = "2026-11-03T15:06:00+00:00"
        maturity_booked = "2026-11-03T16:00:00+00:00"
        final = self._economic_receipt()
        final["event"].update(effective_at=maturity_at, recorded_at=maturity_known,
                              source_at=maturity_known)
        final["entitlement"].update(ex_at="2026-11-03T12:00:00+00:00",
                                    record_at="2026-11-03T13:00:00+00:00",
                                    known_at=maturity_known)
        self._book(economic=final, at=maturity_booked)
        receipt = self._review()
        receipt["paid_obligation_ids"] = ["coupon-obligation", "maturity-obligation"]
        receipt["expected_cash_by_currency"] = [["ARS", "110"]]
        schedule = receipt["issuer_schedule"]
        schedule["maturity_at"] = maturity_at
        schedule["obligations"] = [
            {key: coupon["event"][key] for key in (
                "obligation_id", "kind", "effective_at", "payment_currency",
                "cash_amount", "nominal_base", "nominal_redeemed")},
            {key: final["event"][key] for key in (
                "obligation_id", "kind", "effective_at", "payment_currency",
                "cash_amount", "nominal_base", "nominal_redeemed")},
        ]
        with self.store.connect() as db:
            before = cash_effect_checked(db, "ARS", maturity_booked)
            self.assertEqual(before, Decimal(110))
        first = self._apply(receipt, at=maturity_booked)
        self.assertEqual(self._apply(receipt, at=maturity_booked), first)
        self.assertEqual(first["reconciled_cash_event_count"], 2)
        self.assertEqual(first["principal_by_currency"], [["ARS", "100"]])
        self.assertEqual(first["cash_by_currency"], [["ARS", "110"]])
        with self.store.connect() as db:
            self.assertEqual(cash_effect_checked(db, "ARS", maturity_booked), before)
            self.assertEqual(db.execute("SELECT count(*) FROM paper_fills").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT status FROM paper_positions").fetchone()[0], "OPEN")

    def test_simulated_future_nov_2_maturity_replay_and_pit_no_future_import(self):
        # SIMULATED fixture, not 2-Nov BYMA/issuer evidence or market-open proof.
        future_due = "2026-11-02T15:00:00+00:00"
        future_known = "2026-11-02T15:06:00+00:00"
        future_booked = "2026-11-02T16:00:00+00:00"
        economic = self._economic_receipt()
        economic["event"].update(effective_at=future_due, recorded_at=future_known,
                                 source_at=future_known)
        economic["entitlement"].update(ex_at="2026-11-02T12:00:00+00:00",
                                       record_at="2026-11-02T13:00:00+00:00",
                                       known_at=future_known)
        self._book(economic=economic, at=future_booked)
        review = self._review()
        schedule = review["issuer_schedule"]
        schedule["maturity_at"] = future_due
        schedule["obligations"][0]["effective_at"] = future_due
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_ISSUER_CLOCK_INVALID"):
            self._apply(review, at="2026-10-31T18:00:00+00:00")
        with self.store.connect() as db:
            self.assertEqual(
                position_at(db, "PAPER-AL30", "2026-10-31T18:00:00+00:00").residual_nominal,
                Decimal(100))
        first = self._apply(review, at=future_booked)
        self.assertEqual(first, self._apply(review, at="2026-11-03T16:00:00+00:00"))
        self.assertEqual(first["reconciled_cash_event_count"], 1)
        self.assertEqual(self._review_count(), 1)

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
            (lambda d: d["issuer_schedule"].update(maturity_at="2026-10-30T15:00:00+00:00"),
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
        bad["issuer_schedule"]["known_at"] = "2026-11-01T12:00:00+00:00"
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
        changed["issuer_schedule"]["source_reference"] = "DIFFERENT_REVIEWED_ISSUER_REFERENCE"
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_REVIEW_REPLAY_CONFLICT"):
            self._apply(changed)
        with self.store.connect() as db:
            db.execute("UPDATE paper_fi_economic_events SET receipt_sha256=?",
                       ("b" * 64,))
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_REVIEW_REPLAY_CONFLICT"):
            self._apply()
        self.assertEqual(self._review_count(), 1)

    def test_exact_replay_must_not_backdate_previously_reviewed_knowledge(self):
        self._book()
        later = "2026-10-30T16:00:00+00:00"
        self._apply(at=later)
        with self.assertRaisesRegex(ValueError, "FI_REDEMPTION_REVIEW_CLOCK_ROLLBACK"):
            self._apply(at=BOOKED)
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
