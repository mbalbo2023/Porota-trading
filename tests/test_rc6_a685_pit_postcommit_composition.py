"""A685: OFFHOST composition of A674 Book1 HOLD and A677 pinned signer.

No PPI/provider access, Watch, broker route, live runtime, or PAPER admission.
The synthetic Ed25519 key exists exclusively inside a TemporaryDirectory.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from be_paper_engine import PaperStore, Quote
import rc6_paper_causal_decision_receipt as causal
from rc6_ppi_scanner_provenance import (
    PIT_JOURNAL_FILENAME, capture_native_book_journal,
)
from rc6_ppi_signed_postcommit_pit import inspect_pinned_signed_two_book
from scripts import rc6_paper_postcommit_signer as signer


ART = timezone(timedelta(hours=-3))
AT = datetime(2026, 11, 4, 11, 0, tzinfo=ART)
DECISION_AT = AT + timedelta(seconds=1)
KNOWN_AT = AT + timedelta(seconds=2)
RELEASE = "a" * 64
KEY = "A685:GGAL:BUY:UNBOUND_BOOK1"
IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")


class TestA685Book1SignerComposition(unittest.TestCase):
    def test_unbound_caller_book1_hash_cannot_gain_signed_pit_authority(self):
        """Even a correctly pinned post-COMMIT signature cannot forge Book1."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "paper.db"
            store = PaperStore(str(database))
            journal = root / "ppi" / PIT_JOURNAL_FILENAME
            journal.parent.mkdir()

            private = Ed25519PrivateKey.generate()
            key_path = root / "independent-ed25519-0600.key"
            key_path.write_bytes(private.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            ))
            key_path.chmod(0o600)
            public = private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw,
            )
            key_pin = hashlib.sha256(public).hexdigest()

            def quote(at):
                stamp = at.isoformat()
                return Quote(
                    "GGAL", "ACCIONES", "A-24HS", Decimal("100"),
                    Decimal("100"), Decimal("100.20"), Decimal("14"),
                    Decimal("21"), stamp, currency="ARS", market="BYMA",
                    book_at=stamp, trade_at=stamp, last_kind="TRADE",
                )

            def capture(at):
                q = quote(at)
                raw = {
                    "date": q.book_at,
                    "bids": [{"position": 0, "price": "100", "quantity": "14"}],
                    "offers": [{"position": 0, "price": "100.20", "quantity": "21"}],
                }
                receipt = capture_native_book_journal(
                    journal, q, raw_book=raw,
                    request=("GGAL", "ACCIONES", "A-24HS"),
                    identity=IDENTITY, observed_at=q.observed_at,
                )
                self.assertEqual(receipt["status"], "CAPTURED")
                return receipt

            capture(AT)
            q = quote(AT)
            self.assertIsNone(q.ppi_book1_event_sha256)
            features = {
                "performance_lineage": {"decision_at": DECISION_AT.isoformat()},
                "entry_authority": False,
                "real_routes_used": [],
                # Only a caller-supplied digest, NOT a hash bound by scanner.
                "ppi_book1_event_sha256": "f" * 64,
            }
            with patch.dict(os.environ, {"POROTA_RC6_RELEASE_SHA256": RELEASE}):
                self.assertTrue(store.record_decision(
                    KEY, q, "BUY", Decimal("0.75"), "SYNTHETIC_ONLY", features,
                ))

            with sqlite3.connect(database) as connection:
                frozen, _, error = causal._read_verified_freeze(connection, KEY)
            self.assertIsNone(error)
            self.assertIsNotNone(frozen)
            self.assertIsNone(frozen["book1_event_sha256_claim"])

            with patch.object(causal, "PINNED_DECISION_SIGNER_KEY_SHA256", key_pin), \
                 patch.object(causal, "PINNED_RELEASE_SHA256", RELEASE), \
                 patch.object(causal, "_now", return_value=KNOWN_AT.isoformat()), \
                 patch.object(signer, "PINNED_DECISION_DB_ABSPATH", database):
                signature = signer.attest_one_pinned(
                    database, decision_key=KEY, signing_key_path=key_path,
                    expected_release_sha256=RELEASE,
                )
            # A signature attests the committed decision, not a fabricated Book1.
            self.assertTrue(signature["signature_created"])
            capture(AT + timedelta(seconds=4))

            with patch.object(causal, "PINNED_DECISION_SIGNER_KEY_SHA256", key_pin), \
                 patch.object(causal, "PINNED_RELEASE_SHA256", RELEASE):
                verdict = inspect_pinned_signed_two_book(
                    journal, database, decision_key=KEY,
                    family_group="ACCIONES", identity=IDENTITY,
                    latency_seconds=1, trusted_public_key_raw=public,
                    trusted_public_key_sha256=key_pin,
                    trusted_release_sha256=RELEASE,
                )
            self.assertEqual(verdict["reason"], "PIT_SIGNED_BOOK1_CLAIM_ABSENT")
            self.assertFalse(verdict["book1_quote_bound"])
            self.assertFalse(verdict["book2_after_signed_known_at"])
            self.assertFalse(verdict["family_ready"])
            self.assertFalse(verdict["fill_accredited"])
            self.assertEqual(verdict["provider_gets"], 0)
            self.assertEqual(verdict["real_orders_sent"], 0)


if __name__ == "__main__":
    unittest.main()
