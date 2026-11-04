"""A620 PAPER decision producer: synthetic keys/quotes, no broker/provider calls."""
import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from be_paper_engine import PaperStore, Quote, SOURCE, STRATEGY_VERSION
from bs_instrument_contracts import InstrumentContract
from bv_paper_runtime import broker_from_environment
import rc6_paper_causal_decision_receipt as causal_module
from rc6_paper_causal_decision_receipt import (
    attest_after_commit, freeze_in_decision_transaction, verify_signed_receipt,
)


ART = timezone(timedelta(hours=-3))
QUOTE_AT = datetime(2026, 11, 3, 11, 0, tzinfo=ART)
RELEASE = "a" * 64
KEY = "A620:GGAL:BUY:PAPER"
BOOK1 = "b" * 64


class TestA620CausalDecisionFreeze(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "paper.db"
        self.store = PaperStore(str(self.db))
        self.signer = Ed25519PrivateKey.generate()
        key = self.signer.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
        self.signer_path = Path(self.directory.name) / "observer-key-0600"
        self.signer_path.write_bytes(key)
        self.signer_path.chmod(0o600)
        self.public = self.signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw)
        self.key_pin = hashlib.sha256(self.public).hexdigest()

    def quote(self):
        at = QUOTE_AT.isoformat()
        return Quote("GGAL", "ACCIONES", "A-24HS", Decimal("100"),
                     Decimal("100"), Decimal("100.20"), Decimal("14"), Decimal("21"),
                     at, currency="ARS", market="BYMA", book_at=at,
                     trade_at=at, last_kind="TRADE")

    def features(self, *, seconds=1):
        return {"performance_lineage": {
            "decision_at": (QUOTE_AT + timedelta(seconds=seconds)).isoformat()},
            "ppi_book1_event_sha256": BOOK1,
            "real_routes_used": [], "entry_authority": False}

    def record(self, key=KEY, *, seconds=1, release=RELEASE):
        with patch.dict(os.environ, {"POROTA_RC6_RELEASE_SHA256": release}):
            return self.store.record_decision(
                key, self.quote(), "BUY", Decimal("0.75"),
                "TEST_ONLY", self.features(seconds=seconds))

    def attest(self, key=KEY, *, signer_path=None, release=RELEASE):
        return attest_after_commit(
            self.db, decision_key=key,
            signing_key_path=signer_path if signer_path is not None else self.signer_path,
            expected_release_sha256=release)

    def verify(self, key=KEY, *, public=None, pin=None, release=RELEASE):
        # Simulate a *separately reviewed release policy*, not receipt claims.
        # Real candidate intentionally ships both pins as None => failclosed.
        with patch.object(causal_module, "PINNED_DECISION_SIGNER_KEY_SHA256", self.key_pin), \
             patch.object(causal_module, "PINNED_RELEASE_SHA256", RELEASE):
            return verify_signed_receipt(
                self.db, decision_key=key,
                trusted_public_key_raw=self.public if public is None else public,
                trusted_public_key_sha256=self.key_pin if pin is None else pin,
                trusted_release_sha256=release)

    def freeze(self, key=KEY):
        with self.store.connect() as c:
            row = c.execute("SELECT freeze_payload_json,freeze_sha256,"
                            "prepared_before_commit_at FROM paper_causal_decision_freezes "
                            "WHERE decision_key=?", (key,)).fetchone()
            return (json.loads(row[0]), row[1], row[2]) if row else None

    def test_decision_and_freeze_same_transaction_without_any_key(self):
        self.assertTrue(self.record())
        frozen, sha, prepared = self.freeze()
        self.assertEqual(frozen["schema"], "rc6.paper-decision-causal-freeze.v1")
        self.assertEqual(frozen["identity"], ["GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"])
        self.assertEqual(frozen["action"], "BUY")
        self.assertEqual(frozen["book1_event_sha256_claim"], BOOK1)
        self.assertEqual(frozen["release_sha256_claim"], RELEASE)
        self.assertEqual(hashlib.sha256(json.dumps(frozen, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest(), sha)
        self.assertIsNotNone(prepared)
        self.assertNotIn("decision_known_at", frozen)
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM paper_causal_decision_receipts").fetchone()[0], 0)
        self.assertFalse(self.verify()["decision_known_at_verified"])

    def test_synthetic_ed25519_postcommit_observer_signature_and_pin(self):
        self.assertTrue(self.record())
        attested = self.attest()
        self.assertTrue(attested["signature_created"])
        self.assertEqual(attested["state"], "RECORDED_UNVERIFIED_UNTIL_PINNED_VERIFIER")
        validated = self.verify()
        self.assertTrue(validated["decision_known_at_verified"])
        self.assertTrue(validated["decision_signature_verified"])
        self.assertEqual(validated["state"], "SIGNED_DECISION_KNOWN_AT_VERIFIED")
        self.assertEqual(validated["decision_known_at"], attested["decision_known_at"])
        self.assertGreater(datetime.fromisoformat(validated["decision_known_at"]),
                           QUOTE_AT.astimezone(timezone.utc))
        self.assertFalse(validated["ppi_source_capture_attested"])
        self.assertFalse(validated["unit_contract_verified"])
        self.assertFalse(validated["tariff_contract_verified"])
        self.assertFalse(validated["fill_accredited"])
        self.assertEqual(validated["real_orders_sent"], 0)

    def test_unsigned_freeze_crash_recovery_never_backdates_known_at(self):
        self.assertTrue(self.record())
        unavailable = self.verify()
        self.assertEqual(unavailable["reason"], "CAUSAL_SIGNED_RECEIPT_ABSENT")
        self.assertIsNone(unavailable["decision_known_at"])
        # Simulate an observer restart: read the frozen row later, then sign.
        observed = self.attest()
        self.assertTrue(observed["signature_created"])
        self.assertGreater(datetime.fromisoformat(observed["decision_known_at"]),
                           datetime.fromisoformat(self.freeze()[2]))
        self.assertTrue(self.verify()["decision_known_at_verified"])

    def test_crash_inside_decision_transaction_rolls_back_both_tables(self):
        q, features = self.quote(), self.features()
        raw = json.dumps(features, ensure_ascii=False)
        with self.assertRaises(RuntimeError):
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO paper_decisions VALUES(NULL,?,?,?,?,?,?,?,?,?)",
                                   (SOURCE, STRATEGY_VERSION, KEY, q.observed_at,
                                    q.symbol, "BUY", "0.75", "TEST_ONLY", raw))
                freeze_in_decision_transaction(
                    connection, decision_key=KEY, quote=q, action="BUY",
                    score="0.75", reason="TEST_ONLY", features=features,
                    features_json=raw, source=SOURCE, strategy_version=STRATEGY_VERSION)
                raise RuntimeError("SYNTHETIC_CRASH_BEFORE_COMMIT")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_causal_decision_freezes").fetchone()[0], 0)

    def test_replay_freeze_and_receipt_are_immutable(self):
        self.assertTrue(self.record())
        self.assertFalse(self.record())
        first = self.attest()
        second = self.attest()
        self.assertTrue(first["signature_created"])
        self.assertFalse(second["signature_created"])
        self.assertEqual(second["reason"], "CAUSAL_RECEIPT_ALREADY_COMMITTED_NO_REPLAY")
        with self.store.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE paper_causal_decision_freezes SET freeze_sha256=?",
                                   ("0" * 64,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM paper_causal_decision_receipts")
        self.assertTrue(self.verify()["decision_known_at_verified"])

    def test_forged_public_key_or_release_cannot_authorize_known_at(self):
        self.assertTrue(self.record())
        self.attest()
        wrong_pin = self.verify(pin="f" * 64)
        self.assertFalse(wrong_pin["decision_signature_verified"])
        self.assertIsNone(wrong_pin["decision_known_at"])
        wrong_release = self.verify(release="c" * 64)
        self.assertEqual(wrong_release["state"], "NO_VERIFICADO")
        self.assertEqual(wrong_release["reason"], "CAUSAL_TRUST_ANCHOR_UNAVAILABLE")
        self.assertFalse(wrong_release["decision_known_at_verified"])
        no_key = verify_signed_receipt(
            self.db, decision_key=KEY, trusted_public_key_raw=None,
            trusted_public_key_sha256=None, trusted_release_sha256=None)
        self.assertEqual(no_key["reason"], "CAUSAL_TRUST_ANCHOR_UNAVAILABLE")

    def test_matching_self_signed_key_without_release_pin_is_not_authority(self):
        self.assertTrue(self.record())
        self.attest()
        result = verify_signed_receipt(
            self.db, decision_key=KEY, trusted_public_key_raw=self.public,
            trusted_public_key_sha256=self.key_pin,
            trusted_release_sha256=RELEASE)
        self.assertEqual(result["reason"], "CAUSAL_TRUST_ANCHOR_UNAVAILABLE")
        self.assertFalse(result["decision_known_at_verified"])
        self.assertFalse(result["decision_signature_verified"])
        self.assertFalse(result["fill_accredited"])

    def test_future_decision_clock_blocks_observer_not_backdated(self):
        self.assertTrue(self.record(seconds=3_600_000))
        outcome = self.attest()
        self.assertEqual(outcome["reason"], "CAUSAL_OBSERVER_CLOCK_BEFORE_DECISION_HOLD")
        self.assertFalse(outcome["signature_created"])
        self.assertIsNone(outcome["decision_known_at"])

    def test_private_signer_key_permission_and_release_enforced(self):
        self.assertTrue(self.record())
        self.signer_path.chmod(0o644)
        outcome = self.attest()
        self.assertEqual(outcome["reason"], "CAUSAL_SIGNER_PRIVATE_KEY_PERMISSION_HOLD")
        self.signer_path.chmod(0o600)
        missing_release = self.attest(release="d" * 64)
        self.assertEqual(missing_release["reason"], "CAUSAL_DECISION_CLOCK_IDENTITY_OR_RELEASE_HOLD")
        self.assertFalse(missing_release["signature_created"])

    def test_post_admission_snapshot_does_not_change_prior_freeze_clock(self):
        self.assertTrue(self.record())
        original_freeze = self.freeze()
        attested = self.attest()
        # The legacy gate snapshot can be committed later with captured_at
        # equal to the *old quote clock*. Our immutable freeze/receipt is not
        # reconstructed from this post-admission projection.
        with self.store.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO decision_evidence_snapshots "
                               "VALUES(?,?,?,?,?)", (KEY, QUOTE_AT.isoformat(),
                               "rc6.decision-inputs.v1", "f" * 64,
                               '{"late_snapshot":true}'))
        self.assertEqual(self.freeze(), original_freeze)
        self.assertTrue(self.verify()["decision_known_at_verified"])
        self.assertEqual(self.verify()["decision_known_at"], attested["decision_known_at"])

    def test_missing_release_config_freezes_but_cannot_sign(self):
        with patch.dict(os.environ, {"POROTA_RC6_RELEASE_SHA256": ""}):
            self.assertTrue(self.store.record_decision(
                KEY, self.quote(), "BUY", Decimal("0.75"),
                "TEST_ONLY", self.features()))
        self.assertIsNone(self.freeze()[0]["release_sha256_claim"])
        outcome = self.attest()
        self.assertFalse(outcome["signature_created"])
        self.assertEqual(outcome["reason"], "CAUSAL_DECISION_CLOCK_IDENTITY_OR_RELEASE_HOLD")

    def test_non_actionable_hold_has_no_causal_freeze_or_storage_fanout(self):
        with patch.dict(os.environ, {"POROTA_RC6_RELEASE_SHA256": RELEASE}):
            self.assertTrue(self.store.record_decision(
                "A620:GGAL:HOLD:PAPER", self.quote(), "HOLD", Decimal("0.1"),
                "NO_SIGNAL", self.features()))
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_causal_decision_freezes").fetchone()[0], 0)
        self.assertFalse(self.verify("A620:GGAL:HOLD:PAPER")["fill_accredited"])

    def test_post_freeze_paper_decision_row_tamper_denies_signature(self):
        self.assertTrue(self.record())
        with self.store.connect() as connection:
            connection.execute("UPDATE paper_decisions SET reason=? WHERE decision_key=?",
                               ("FALSIFIED", KEY))
        result = self.attest()
        self.assertEqual(result["reason"], "CAUSAL_FREEZE_PAPER_DECISION_LINK_HOLD")
        self.assertFalse(result["signature_created"])
        self.assertFalse(self.verify()["decision_known_at_verified"])

    def test_signature_bytes_tamper_fails_pinned_verifier(self):
        self.assertTrue(self.record())
        self.attest()
        with self.store.connect() as connection:
            # A privileged DB tamperer that removes an immutable trigger
            # still cannot forge an Ed25519 signature under the release pin.
            connection.execute("DROP TRIGGER rc6_causal_receipts_no_update")
            connection.execute("UPDATE paper_causal_decision_receipts SET signature_b64=?",
                               ("AAAA",))
        result = self.verify()
        self.assertEqual(result["state"], "BLOQUEADO")
        self.assertFalse(result["decision_signature_verified"])
        self.assertFalse(result["fill_accredited"])

    def test_validly_signed_receipt_cannot_rebind_book1_claim_from_freeze(self):
        """Even a valid test-key signature cannot contradict the COMMIT freeze."""
        self.assertTrue(self.record())
        self.assertTrue(self.attest()["signature_created"])
        frozen, _, _ = self.freeze()
        self.assertEqual(frozen["book1_event_sha256_claim"], BOOK1)
        with self.store.connect() as connection:
            # Privileged synthetic signer/key compromise, deliberately stronger
            # than an invalid signature or a broken local SHA digest.
            connection.execute("DROP TRIGGER rc6_causal_receipts_no_update")
            original = connection.execute(
                "SELECT payload_json FROM paper_causal_decision_receipts "
                "WHERE decision_key=?", (KEY,)).fetchone()[0]
            forged = json.loads(original)
            forged["book1_event_sha256_claim"] = "c" * 64
            raw = json.dumps(forged, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
            signature = base64.b64encode(self.signer.sign(raw.encode("utf-8"))).decode("ascii")
            self.signer.public_key().verify(base64.b64decode(signature), raw.encode("utf-8"))
            connection.execute(
                "UPDATE paper_causal_decision_receipts "
                "SET payload_json=?,payload_sha256=?,signature_b64=? "
                "WHERE decision_key=?",
                (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(), signature, KEY))
        result = self.verify()
        self.assertEqual(result["state"], "BLOQUEADO")
        self.assertEqual(result["reason"], "CAUSAL_RECEIPT_BINDING_OR_CLOCK_HOLD")
        self.assertFalse(result["decision_known_at_verified"])
        self.assertFalse(result["decision_signature_verified"])
        self.assertFalse(result["fill_accredited"])

    def test_multiple_decisions_have_sequenced_previous_hash_receipts(self):
        self.assertTrue(self.record())
        first = self.attest()
        self.assertTrue(self.record("A620:GGAL:BUY:SECOND"))
        second = self.attest("A620:GGAL:BUY:SECOND")
        self.assertTrue(second["signature_created"])
        with self.store.connect() as connection:
            rows = connection.execute("SELECT sequence,previous_receipt_sha256 "
                                      "FROM paper_causal_decision_receipts ORDER BY sequence").fetchall()
            self.assertEqual([r[0] for r in rows], [1, 2])
            self.assertIsNone(rows[0][1])
            self.assertEqual(rows[1][1], first["receipt_sha256"])
        self.assertTrue(self.verify("A620:GGAL:BUY:SECOND")["decision_signature_verified"])

    def test_spot_native_runtime_path_sets_real_decision_at_before_gate(self):
        """Exercise the real broker on_quote path; veto after decision, no fill."""
        at = (QUOTE_AT + timedelta(seconds=1)).isoformat()
        normalized_at = (QUOTE_AT + timedelta(seconds=1)).astimezone(timezone.utc).isoformat(
            timespec="microseconds")
        broker = broker_from_environment(self.store, clock_fn=lambda: at,
                                         require_supervisor=False, ai_mode="OFF")
        broker.decide = lambda _q: ("BUY", Decimal("0.8"), "SYNTHETIC_NATIVE_SIGNAL",
                                    {"performance_lineage": {"strategy_id": "A620_INTEGRATION"}})
        with patch.dict(os.environ, {"POROTA_RC6_RELEASE_SHA256": RELEASE}):
            broker.on_quote(self.quote(), allow_new_openings=False,
                            opening_block_reason="TEST_NO_ENTRY",
                            evaluate_when_blocked=True)
        with self.store.connect() as connection:
            decisions = connection.execute("SELECT decision_key FROM paper_decisions "
                                           "WHERE action='BUY'").fetchall()
            self.assertEqual(len(decisions), 1)
            frozen = self.freeze(decisions[0][0])[0]
            self.assertEqual(frozen["claimed_decision_at"], normalized_at)
            self.assertEqual(frozen["quote_observed_at"], QUOTE_AT.isoformat())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)

    def test_future_native_runtime_path_sets_real_decision_at_before_gate(self):
        """Future-specific _on_future_quote also calls mark_lineage before store."""
        at = (QUOTE_AT + timedelta(seconds=1)).isoformat()
        normalized_at = (QUOTE_AT + timedelta(seconds=1)).astimezone(timezone.utc).isoformat(
            timespec="microseconds")
        spec = InstrumentContract(
            "DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
            Decimal("1000"), Decimal("1"), "CONTRACT_EVIDENCE_V2_BOUND",
            expires_at="2026-11-30T15:00:00-03:00",
            minimum_quantity=Decimal("1"),
            paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
            paper_margin_rate=Decimal("1"), underlying="DOLAR_A3500")
        q = Quote("DLR/OCT26", "FUTUROS", "INMEDIATA", Decimal("1500"),
                  Decimal("1499"), Decimal("1500"), Decimal("10"), Decimal("10"),
                  QUOTE_AT.isoformat(), contract=spec, currency="ARS", market="A3",
                  metadata_source="PPI_CATALOG:SYNTHETIC", book_at=QUOTE_AT.isoformat(),
                  trade_at=QUOTE_AT.isoformat(), last_kind="TRADE")
        broker = broker_from_environment(self.store, clock_fn=lambda: at,
                                         require_supervisor=False, ai_mode="OFF")
        broker._decide_future = lambda _q: ("BUY", Decimal("0.8"), "SYNTHETIC_FUTURE_SIGNAL",
                                            {"performance_lineage": {"strategy_id": "A620_FUTURE"}})
        with patch.dict(os.environ, {"POROTA_RC6_RELEASE_SHA256": RELEASE}):
            broker.on_quote(q, allow_new_openings=False,
                            opening_block_reason="TEST_NO_FUTURE_ENTRY",
                            evaluate_when_blocked=True)
        with self.store.connect() as connection:
            keys = connection.execute("SELECT decision_key FROM paper_decisions "
                                      "WHERE action='BUY'").fetchall()
            self.assertEqual(len(keys), 1)
            frozen = self.freeze(keys[0][0])[0]
            self.assertEqual(frozen["claimed_decision_at"], normalized_at)
            self.assertEqual(frozen["identity"], ["DLR/OCT26", "FUTUROS", "A3", "ARS", "INMEDIATA"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_future_positions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
