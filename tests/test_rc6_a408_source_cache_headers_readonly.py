"""Synthetic offline header-only checks, no real host, DB, broker or secrets."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "a408_cache", ROOT / "scripts/rc6_a408_source_cache_headers_readonly.py")
probe = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(probe)


class CacheHeaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime.now(timezone.utc)

    def write(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_primary_map_only_counts_no_ticker_or_quote_leak(self):
        p = self.write("primary.json", {
            "captured_at": self.now.isoformat(),
            "secrets": "SECRET_EXPLICIT_NEVER_LOG",
            "quotes_by_symbol": {
                "PRIVATE_GGAL": {"instrument_type": "ACCIONES", "market": "BCBA",
                                 "currency": "ARS", "settlement": "T1",
                                 "trade_at": self.now.isoformat(), "last": 101.11,
                                 "secret_token": "NEVER_SHOW_TOKEN"},
                "PRIVATE_CEDEAR": {"last": 300.45},
                "PRIVATE_SCALAR": 5.2,
            },
        })
        receipt = probe.inspect_cache("PRIMARY_MARKET", p, "PRIMARY", current=self.now)
        self.assertEqual(receipt["status"], "HEADER_READ")
        self.assertEqual(receipt["shape"], "PRIMARY_SYMBOL_MAP")
        self.assertEqual(receipt["records"], 3)
        self.assertEqual(receipt["dictionary_records"], 2)
        self.assertEqual(receipt["identity_fields_complete"], 1)
        self.assertEqual(receipt["native_clock_present"], 1)
        self.assertEqual(receipt["receipt_clock_present"], 3)
        self.assertEqual(receipt["numeric_present"], 3)
        serialized = json.dumps(receipt)
        for secret in ("SECRET_EXPLICIT_NEVER_LOG", "PRIVATE_GGAL", "PRIVATE_CEDEAR",
                       "NEVER_SHOW_TOKEN", "101.11"):
            self.assertNotIn(secret, serialized)

    def test_primary_empty_does_not_create_fake_ready(self):
        p = self.write("empty.json", {"quotes_by_symbol": {}, "last_by_symbol": {}})
        result = probe.inspect_cache("PRIMARY_MARKET", p, "PRIMARY", current=self.now)
        self.assertEqual(result["shape"], "PRIMARY_SYMBOL_MAP")
        self.assertEqual(result["records"], 0)
        self.assertNotIn("READY", json.dumps(result))

    def test_iol_and_byma_only_allowlist_source_records(self):
        iol = self.write("iol.json", {"symbols": [
            {"symbol": "SECRET_IOL", "instrument_type": "BONOS", "market": "BCBA",
             "currency": "ARS", "term": "T1", "timestamp": self.now.isoformat(),
             "received_at": self.now.isoformat(), "last": 1.0},
            {"symbol": "OTHER", "last": 2.0},
        ]})
        byma = self.write("public.json", {"sources": [
            {"source": "CNV", "records": [{"secret": "IGNORE_CNV"}]},
            {"source": "BYMA", "records": [{"symbol": "SECRET_BYMA", "last": 9.0}]},
        ]})
        a = probe.inspect_cache("IOL_SHADOW", iol, "IOL", current=self.now)
        b = probe.inspect_cache("BYMA_PUBLIC", byma, "BYMA", current=self.now)
        self.assertEqual((a["records"], a["identity_fields_complete"],
                          a["native_clock_present"]), (2, 1, 1))
        self.assertEqual((b["records"], b["numeric_present"]), (1, 1))
        self.assertNotIn("SECRET", json.dumps((a, b)))

    def test_missing_invalid_json_and_untrusted_parent_are_fail_closed(self):
        missing = self.root / "missing.json"
        self.assertEqual(probe.inspect_cache("IOL_SHADOW", missing, "IOL", current=self.now)["status"], "MISSING")
        broken = self.root / "broken.json"
        broken.write_text("{not-json}")
        self.assertEqual(probe.inspect_cache("IOL_SHADOW", broken, "IOL", current=self.now)["status"], "JSON_INVALID")
        outside = self.root / "outside.json"
        outside.write_text("{}")
        link = self.root / "parent"
        link.symlink_to(self.root, target_is_directory=True)
        result = probe.inspect_cache("PRIMARY_MARKET", link / "outside.json", "PRIMARY", current=self.now)
        self.assertNotEqual(result["status"], "HEADER_READ")
        hard = self.root / "hard.json"
        os.link(outside, hard)
        self.assertEqual(probe.inspect_cache("PRIMARY_MARKET", hard, "PRIMARY", current=self.now)["status"], "FILE_UNSAFE")

    def test_size_and_future_file_clock_fail_closed(self):
        p = self.root / "large.json"
        p.write_bytes(b"x" * 128)
        self.assertEqual(probe.inspect_cache("IOL_SHADOW", p, "IOL", current=self.now)["status"], "JSON_INVALID")
        with self.assertRaisesRegex(probe.CacheRejected, "SIZE_INVALID"):
            probe._safe_bytes(p, max_bytes=64)
        p = self.write("future.json", {"symbols": []})
        future = (self.now + timedelta(minutes=5)).timestamp()
        os.utime(p, (future, future))
        self.assertEqual(probe.inspect_cache("IOL_SHADOW", p, "IOL", current=self.now)["status"], "FILE_CLOCK_FUTURE")

    def test_public_report_is_diagnostic_never_paper_authority(self):
        p = self.write("primary.json", {"last_by_symbol": {"PRIVATE_ONE": 5}})
        report = probe.inspect_all(current=self.now, inputs=(
            ("PRIMARY_MARKET", p, "PRIMARY"),
            ("PRIMARY_APP", self.root/"missing-app.json", "PRIMARY"),
            ("IOL_SHADOW", self.root/"missing-iol.json", "IOL"),
            ("BYMA_PUBLIC", self.root/"missing-byma.json", "BYMA"),
        ))
        self.assertEqual(report["schema"], probe.SCHEMA)
        self.assertEqual(report["market_readiness"], "NO_VERIFICADO")
        self.assertFalse(report["source_contract_ready"])
        self.assertFalse(report["runtime_sha_verified"])
        self.assertFalse(report["db_opened"])
        self.assertFalse(report["admission_authority"])
        self.assertFalse(report["order_routes_called"])
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(len(report["cache_headers"]), 4)
        self.assertNotIn("PRIVATE_ONE", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
