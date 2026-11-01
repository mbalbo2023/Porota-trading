"""A352: external cryptographic receipts are not causal PAPER authority.

Fixtures are entirely synthetic.  No source, host, order or account access.
These regressions guard the A331/A320/A341 trust boundary without modifying
the existing verifier or the still-Draft A320 implementation.
"""

import base64
from datetime import datetime, timezone
from hashlib import sha256
import hmac
from pathlib import Path
import sys
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_a341_external_sensor_verify as a341


IDENTITY = ["GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"]
NOW = datetime(2026, 11, 1, 22, 15, tzinfo=timezone.utc)


def signed_pair(*, historic=False, strongest_labels=False):
    """Make two authentic test signatures, never real provider receipts."""
    keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
    registry = {"schema": a341.REGISTRY_SCHEMA, "sensors": []}
    receipts = []
    for i, (source, owner, domain) in enumerate((
            ("PPI", "mock-ppi-owner", "mock-ppi-domain"),
            ("IOL", "mock-iol-owner", "mock-iol-domain"))):
        pub = keys[i].public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        registry["sensors"].append({
            "sensor_id": f"mock-sensor-{i}", "source": source,
            "key_id": f"mock-key-{i}", "operator_id": owner,
            "trust_domain": domain,
            "public_key_b64": base64.b64encode(pub).decode(),
            "valid_from_utc": "2026-10-29T00:00:00Z",
            "valid_until_utc": "2026-11-03T00:00:00Z",
        })
        provider_at = "2026-10-30T16:00:00Z" if historic else "2026-11-01T22:14:50Z"
        first_known = "2026-10-30T16:00:02Z" if historic else "2026-11-01T22:14:52Z"
        body = {
            "schema": a341.RECEIPT_SCHEMA,
            "sensor_id": f"mock-sensor-{i}", "source": source,
            "identity": IDENTITY.copy(),
            "provider_at_utc": provider_at,
            "first_known_at_utc": first_known,
            "payload_sha256": "a" * 64 if strongest_labels else str(i + 1) * 64,
            "payload_format": (
                "RAW_HTTP_ATTESTED" if strongest_labels else "SDK_PARSED"
            ),
        }
        body["attestation"] = {
            "algorithm": "Ed25519", "key_id": f"mock-key-{i}",
            "signature_b64": base64.b64encode(keys[i].sign(a341._canonical(body))).decode(),
        }
        receipts.append(body)
    return registry, receipts


def review(registry, receipts):
    return a341.verify_pair(
        registry, a341._digest(registry), receipts, IDENTITY, now=NOW,
    )


class TestSourcePitAttestationBoundaries(unittest.TestCase):
    def test_historic_dual_signatures_do_not_backdate_causal_pit(self):
        registry, receipts = signed_pair(historic=True)
        result = review(registry, receipts)
        self.assertTrue(result["cryptographic_pair_valid"])
        self.assertFalse(result["external_independence_attested"])
        self.assertEqual(0, result["source_pit_verified"])
        self.assertEqual(0, result["paper_sim_verified"])
        self.assertEqual("NONE", result["ready_implication"])
        self.assertEqual("NO_VERIFICADO", result["evidence_state"])

    def test_source_labels_and_matching_hashes_do_not_prove_economics(self):
        registry, receipts = signed_pair(strongest_labels=True)
        result = review(registry, receipts)
        self.assertTrue(result["cryptographic_pair_valid"])
        self.assertEqual(["IOL", "PPI"], result["source_lineages"])
        self.assertFalse(result["external_independence_attested"])
        self.assertEqual(0, result["source_pit_verified"])
        self.assertEqual(0, result["paper_sim_verified"])
        self.assertEqual("NONE", result["ready_implication"])

    def test_legacy_a320_hmac_cannot_substitute_for_public_sensor_signature(self):
        registry, receipts = signed_pair()
        body = {k: v for k, v in receipts[0].items() if k != "attestation"}
        receipts[0]["attestation"] = {
            "algorithm": "HMAC-SHA256", "key_id": "mock-key-0",
            "mac": hmac.new(b"synthetic-offhost-key-32-bytes--!!", a341._canonical(body),
                            sha256).hexdigest(),
        }
        with self.assertRaisesRegex(ValueError, "A341_SIGNATURE_TYPE_OR_KEY_UNVERIFIED"):
            review(registry, receipts)

    def test_a331_history_receipt_cannot_masquerade_as_live_sensor(self):
        registry, receipts = signed_pair()
        receipts[0] = {
            "schema": "rc6.a331.action.sanitized.v1",
            "source": "PPI", "provider_requests": 2,
            "rows_in_day": 1, "first_known_at_utc": "2026-11-01T22:11:37Z",
            "causal_pit_rows_verified": 0, "paper_sim_rows_verified": 0,
            "attestation": receipts[0]["attestation"],
        }
        with self.assertRaisesRegex(ValueError, "A341_RECEIPT_SHAPE_INVALID"):
            review(registry, receipts)


if __name__ == "__main__":
    unittest.main()
