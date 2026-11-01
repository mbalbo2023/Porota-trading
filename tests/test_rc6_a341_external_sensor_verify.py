"""A341: signature checks are real; sensor data and keys are synthetic only."""

import base64
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_a341_external_sensor_verify as a341


NOW = datetime(2026, 11, 1, 20, 0, 10, tzinfo=timezone.utc)
IDENTITY = ["GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"]


def fresh():
    private = [Ed25519PrivateKey.generate() for _ in range(2)]
    sensors = []
    receipts = []
    for i, (source, operator, domain) in enumerate((
            ("PPI", "official-sensor-owner", "PPI-official"),
            ("IOL", "independent-sensor-owner", "IOL-independent"))):
        pub = private[i].public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        sensors.append({"sensor_id": "sensor-" + str(i), "source": source,
                        "key_id": "key-" + str(i), "operator_id": operator,
                        "trust_domain": domain,
                        "public_key_b64": base64.b64encode(pub).decode(),
                        "valid_from_utc": "2026-10-31T00:00:00Z",
                        "valid_until_utc": "2026-11-02T00:00:00Z"})
        body = {"schema": a341.RECEIPT_SCHEMA, "sensor_id": "sensor-" + str(i),
                "source": source, "identity": IDENTITY.copy(),
                "provider_at_utc": "2026-11-01T20:00:00Z",
                "first_known_at_utc": "2026-11-01T20:00:03Z",
                "payload_sha256": "a" * 64 if i == 0 else "b" * 64,
                "payload_format": "SDK_PARSED" if i == 0 else "INDEPENDENT_FEED"}
        signature = private[i].sign(a341._canonical(body))
        body["attestation"] = {"algorithm": "Ed25519", "key_id": "key-" + str(i),
                               "signature_b64": base64.b64encode(signature).decode()}
        receipts.append(body)
    registry = {"schema": a341.REGISTRY_SCHEMA, "sensors": sensors}
    return registry, receipts, private


def sign(receipts, private, index):
    receipt = receipts[index]
    body = {key: value for key, value in receipt.items() if key != "attestation"}
    receipt["attestation"]["signature_b64"] = base64.b64encode(
        private[index].sign(a341._canonical(body))).decode()


def verify(registry, receipts, identity=IDENTITY):
    return a341.verify_pair(registry, a341._digest(registry), receipts, identity,
                            now=NOW)


class TestIndependentPublicSensorVerifier(unittest.TestCase):
    def setUp(self):
        self.registry, self.receipts, self.private = fresh()

    def assert_rejected(self, expected):
        with self.assertRaisesRegex(ValueError, expected):
            verify(self.registry, self.receipts)

    def test_real_crypto_synthetic_pair_never_promotes_business_readiness(self):
        result = verify(self.registry, self.receipts)
        self.assertTrue(result["cryptographic_pair_valid"])
        self.assertEqual(2, result["signed_receipts"])
        self.assertEqual(["IOL", "PPI"], result["source_lineages"])
        self.assertEqual("NO_VERIFICADO", result["evidence_state"])
        self.assertFalse(result["external_independence_attested"])
        self.assertEqual(0, result["source_pit_verified"])
        self.assertEqual(0, result["paper_sim_verified"])
        self.assertEqual(0, result["real_orders_sent"])
        self.assertEqual("NONE", result["ready_implication"])
        exported = json.dumps(result)
        for field in ("public_key_b64", "signature_b64", "price", "token", "account"):
            self.assertNotIn(field, exported)

    def test_sensor_signature_cannot_be_reused_for_changed_body(self):
        self.receipts[0]["payload_sha256"] = "c" * 64
        self.assert_rejected("A341_SIGNATURE_INVALID")

    def test_registry_pin_is_not_self_authenticating(self):
        old_pin = a341._digest(self.registry)
        self.registry["sensors"][0]["operator_id"] = "forged-operator"
        with self.assertRaisesRegex(ValueError, "A341_TRUST_REGISTRY_PIN_UNVERIFIED"):
            a341.verify_pair(self.registry, old_pin, self.receipts, IDENTITY, now=NOW)

    def test_same_operator_or_same_trust_domain_is_not_independent(self):
        for field in ("operator_id", "trust_domain"):
            altered = copy.deepcopy(self.registry)
            altered["sensors"][1][field] = altered["sensors"][0][field]
            with self.assertRaisesRegex(ValueError, "A341_SENSORS_NOT_INDEPENDENT"):
                verify(altered, self.receipts)

    def test_same_source_lineage_iol_and_connector_cannot_cross_attest(self):
        self.registry["sensors"][0]["source"] = "IOL_CONNECTOR"
        self.receipts[0]["source"] = "IOL_CONNECTOR"
        sign(self.receipts, self.private, 0)
        self.assert_rejected("A341_SENSORS_NOT_INDEPENDENT")

    def test_renaming_the_same_public_key_does_not_create_two_trust_roots(self):
        self.registry["sensors"][1]["public_key_b64"] = self.registry["sensors"][0]["public_key_b64"]
        self.receipts[1]["attestation"]["signature_b64"] = base64.b64encode(
            self.private[0].sign(a341._canonical({key: value for key, value in
                self.receipts[1].items() if key != "attestation"}))).decode()
        self.assert_rejected("A341_SENSORS_NOT_INDEPENDENT")

    def test_duplicate_sensor_id_or_key_is_not_independent(self):
        self.registry["sensors"][1]["sensor_id"] = "sensor-0"
        self.assert_rejected("A341_TRUST_SENSOR_NOT_UNIQUE")

    def test_full_five_field_identity_rejects_cross_currency_and_settlement(self):
        for index, value in ((3, "USD"), (4, "CI"), (1, "BONOS")):
            altered = IDENTITY.copy()
            altered[index] = value
            with self.assertRaisesRegex(ValueError, "A341_RECEIPT_IDENTITY_INVALID"):
                verify(self.registry, self.receipts, altered)

    def test_provider_future_naive_offset_and_stale_are_rejected(self):
        for field, value, reason in (
            ("provider_at_utc", "2026-11-01T20:00:11Z", "A341_QUOTE_STALE_OR_FUTURE"),
            ("first_known_at_utc", "2026-11-01T20:00:11Z", "A341_QUOTE_STALE_OR_FUTURE"),
            ("provider_at_utc", "2026-11-01T19:57:00Z", "A341_QUOTE_STALE_OR_FUTURE"),
            ("provider_at_utc", "2026-11-01T20:00:00", "A341_PROVIDER_CLOCK_INVALID"),
            ("first_known_at_utc", "2026-11-01T20:00:03", "A341_FIRST_KNOWN_CLOCK_INVALID"),
        ):
            receipt = copy.deepcopy(self.receipts)
            receipt[0][field] = value
            sign(receipt, self.private, 0)
            with self.assertRaisesRegex(ValueError, reason):
                verify(self.registry, receipt)

    def test_signature_key_expired_even_when_crypto_is_valid(self):
        self.registry["sensors"][0]["valid_until_utc"] = "2026-11-01T20:00:02Z"
        self.assert_rejected("A341_SIGNER_OUTSIDE_VALIDITY")

    def test_extra_raw_payload_or_account_is_rejected_before_output(self):
        self.receipts[0]["raw_payload"] = {"price": 123, "account": "SECRET"}
        self.assert_rejected("A341_RECEIPT_SHAPE_INVALID")

    def test_unknown_key_and_malformed_signature_are_rejected(self):
        for signature in ("not-base64", base64.b64encode(b"a" * 63).decode()):
            altered = copy.deepcopy(self.receipts)
            altered[0]["attestation"]["signature_b64"] = signature
            with self.assertRaisesRegex(ValueError, "A341_SIGNATURE_LENGTH_INVALID"):
                verify(self.registry, altered)
        self.receipts[0]["attestation"]["key_id"] = "not-our-key"
        self.assert_rejected("A341_SIGNATURE_TYPE_OR_KEY_UNVERIFIED")

    def test_untrusted_registry_public_key_cannot_verify_signature(self):
        other = Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.registry["sensors"][0]["public_key_b64"] = base64.b64encode(other).decode()
        self.assert_rejected("A341_SIGNATURE_INVALID")

    def test_file_reader_denies_duplicate_keys_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            double = root / "duplicate.json"
            double.write_text('{"schema":"one","schema":"two"}')
            with self.assertRaisesRegex(ValueError, "A341_DUPLICATE_JSON_KEYS"):
                a341._read_json(double, 1024)
            alias = root / "alias.json"
            alias.symlink_to(double)
            with self.assertRaisesRegex(ValueError, "A341_INPUT_FILE_UNSAFE"):
                a341._read_json(alias, 1024)

    def test_file_reader_deny_oversized_and_nonfinite(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.json"
            path.write_text("[" + "0," * 200 + "0]")
            with self.assertRaisesRegex(ValueError, "A341_INPUT_FILE_UNSAFE"):
                a341._read_json(path, 50)
            path.write_text('{"x":NaN}')
            with self.assertRaisesRegex(ValueError, "A341_NONFINITE_JSON_NUMBER"):
                a341._read_json(path, 100)

    def test_cli_catches_error_without_leaking_secret_or_provider_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.json"
            receipt = root / "receipt.json"
            registry.write_text(json.dumps(self.registry))
            receipt.write_text(json.dumps([{**self.receipts[0], "account": "SECRET_ACCT"},
                                           self.receipts[1]]))
            output = io.StringIO()
            with redirect_stdout(output):
                code = a341.main(["--registry", str(registry),
                                  "--registry-sha256", a341._digest(self.registry),
                                  "--receipts", str(receipt),
                                  "--identity", json.dumps(IDENTITY)])
            self.assertEqual(2, code)
            self.assertNotIn("SECRET_ACCT", output.getvalue())
            self.assertEqual(0, json.loads(output.getvalue())["real_orders_sent"])

    def test_crypto_only_never_converts_ten_synthetic_families_into_live_approvals(self):
        for family in sorted(a341.FAMILIES):
            identity = [IDENTITY[0], family, IDENTITY[2], IDENTITY[3], IDENTITY[4]]
            pair = copy.deepcopy(self.receipts)
            for i in range(2):
                pair[i]["identity"] = identity.copy()
                sign(pair, self.private, i)
            report = verify(self.registry, pair, identity)
            self.assertTrue(report["cryptographic_pair_valid"])
            self.assertEqual(0, report["source_pit_verified"])
            self.assertEqual(0, report["paper_sim_verified"])


if __name__ == "__main__":
    unittest.main()
