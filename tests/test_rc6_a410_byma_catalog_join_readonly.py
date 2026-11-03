"""A410 contracts: exact identity, hostile files, privacy and no admission."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from datetime import datetime, timedelta, timezone


PATH = Path(__file__).resolve().parents[1] / "scripts/rc6_a410_byma_catalog_join_readonly.py"
spec = importlib.util.spec_from_file_location("a410_probe", PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

PRIVATE = "PRIVATE_TEST_DO_NOT_EXPORT"


def canonical(symbol=PRIVATE, family="ACCIONES", market="BYMA",
              currency="ARS", settlement="A-24HS"):
    return [symbol, family, market, currency, settlement]


def byma_record(symbol=PRIVATE, **overrides):
    row = {"ticker": symbol, "family": "ACCION", "market": "BCBA",
           "currency": "ARS", "term": "T1",
           "last": "PRIVATE_PRICE_DO_NOT_EXPORT"}
    row.update(overrides)
    return row


class A410JoinContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.catalog_path = self.folder / "catalog.json"
        self.byma_path = self.folder / "byma.json"
        self.now = datetime.now(timezone.utc)
        self.catalog = {"schema": probe.CATALOG_SCHEMA,
                        "instruments": [{"canonical_identity": canonical()}]}
        self.byma = {"schema": probe.BYMA_SCHEMA,
                     "sources": [{"source": "BYMA", "records": [byma_record()]}]}

    def write(self, catalog=None, byma=None, *, age=45):
        if catalog is None:
            catalog = self.catalog
        if byma is None:
            byma = self.byma
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        self.byma_path.write_text(json.dumps(byma), encoding="utf-8")
        age_time = (self.now - timedelta(seconds=age)).timestamp()
        os.utime(self.catalog_path, (age_time, age_time))
        os.utime(self.byma_path, (age_time, age_time))

    def inspect(self):
        return probe.inspect_files(self.catalog_path, self.byma_path, now=self.now)

    def test_alias_match_is_five_key_and_never_ready(self):
        self.byma["captured_at"] = "PRIVATE_RECEIPT_TIMESTAMP"
        self.byma["sources"][0]["records"][0]["provider_observed_at"] = "2026-11-02T18:01:00+00:00"
        self.write()
        result = self.inspect()
        self.assertEqual(result["diagnostic_status"], "JOIN_COUNTS_ONLY")
        self.assertEqual(result["reason"], "EXACT_IDENTITY_DIAGNOSTIC_ONLY")
        self.assertEqual(result["counts"]["byma_exact_rows"], 1)
        self.assertEqual(result["counts"]["byma_exact_unique"], 1)
        self.assertEqual(result["counts"]["byma_native_clock_aware"], 1)
        self.assertEqual(result["counts"]["byma_receipt_clock_present"], 1)
        self.assertEqual(result["byma_matched_by_family"]["ACCIONES"], 1)
        self.assertEqual(result["market_readiness"], "NO_VERIFICADO")
        self.assertFalse(result["source_contract_ready"])
        self.assertFalse(result["causal_pit_verified"])
        self.assertFalse(result["runtime_sha_verified"])
        self.assertFalse(result["host_writes"])
        self.assertFalse(result["db_opened"])
        self.assertFalse(result["order_routes_called"])
        self.assertEqual(result["provider_calls"], 0)
        text = json.dumps(result)
        self.assertNotIn(PRIVATE, text)
        self.assertNotIn("PRIVATE_PRICE_DO_NOT_EXPORT", text)
        self.assertNotIn("PRIVATE_RECEIPT_TIMESTAMP", text)
        self.assertNotIn("2026-11-02T18:01:00+00:00", text)
        self.assertNotIn(str(self.folder), text)

    def test_mismatch_dimensions_duplicate_and_missing(self):
        rows = [byma_record(), byma_record(),
                byma_record(settlement="CI", term="CI"),
                byma_record("PRIVATE_UNKNOWN_SYMBOL"),
                byma_record(family="BONO", instrument_type="BONO"),
                byma_record(market="A3"),
                byma_record(currency="USD")]
        # Remove contradictory term fields from the settlement alias fixture.
        del rows[2]["term"]
        self.byma["sources"][0]["records"] = rows
        self.write()
        counts = self.inspect()["counts"]
        self.assertEqual(counts["byma_rows"], 7)
        self.assertEqual(counts["byma_exact_rows"], 2)
        self.assertEqual(counts["byma_exact_unique"], 1)
        self.assertEqual(counts["byma_unmatched_key"], 5)
        self.assertEqual(counts["byma_symbol_absent"], 1)
        self.assertEqual(counts["byma_same_symbol_different_key"], 4)
        self.assertEqual(counts["different_family"], 1)
        self.assertEqual(counts["different_market"], 1)
        self.assertEqual(counts["different_currency"], 1)
        self.assertEqual(counts["different_settlement"], 1)
        self.assertEqual(counts["byma_multiple_keys_same_symbol"], 1)

    def test_alias_conflict_unknown_code_and_nested_quote_fail_closed(self):
        self.byma["sources"][0]["records"] = [
            byma_record(family="ACCION", instrument_type="ON"),
            byma_record(settlement="T1", term="CI"),
            dict(byma_record(), symbol="PRIVATE_DIFFERENT_SYMBOL"),
            byma_record(market="PRIVATE_EXCHANGE"),
            byma_record(currency="PRIVATE_CURRENCY"),
            byma_record(family="PRIVATE_FAMILY"),
            {"quote": byma_record()},
            byma_record("UNKNOWN"),
        ]
        # ticker still present in symbol-conflict row; trigger two distinct aliases.
        self.write()
        result = self.inspect()
        reasons = result["reject_counts"]
        self.assertEqual(reasons["FAMILY_CONFLICT"], 1)
        self.assertEqual(reasons["SETTLEMENT_CONFLICT"], 1)
        self.assertEqual(reasons["SYMBOL_CONFLICT"], 1)
        self.assertEqual(reasons["UNKNOWN_MARKET"], 1)
        self.assertEqual(reasons["UNKNOWN_CURRENCY"], 1)
        self.assertEqual(reasons["UNKNOWN_FAMILY"], 1)
        self.assertEqual(reasons["MISSING_SYMBOL"], 2)
        self.assertEqual(result["counts"]["byma_key_valid"], 0)
        self.assertEqual(result["counts"]["byma_nested_quote_rows"], 1)

    def test_blank_nonstring_and_unknown_symbols_fail_closed(self):
        invalid = ("", " ", "\t", "UNKNOWN", " unknown ", 0, 42, True,
                   [], {"ticker": "PRIVATE_UNTRUSTED_SYMBOL"})
        for index, value in enumerate(invalid):
            with self.subTest(case=index, invalid_symbol_type=type(value).__name__):
                self.byma["sources"][0]["records"] = [byma_record(value)]
                self.write()
                receipt = self.inspect()
                self.assertEqual(receipt["diagnostic_status"], "JOIN_COUNTS_ONLY")
                self.assertEqual(receipt["counts"]["byma_rows"], 1)
                self.assertEqual(receipt["counts"]["byma_key_valid"], 0)
                self.assertEqual(receipt["counts"]["byma_key_invalid"], 1)
                self.assertEqual(receipt["reject_counts"]["MISSING_SYMBOL"], 1)
                self.assertEqual(receipt["market_readiness"], "NO_VERIFICADO")
                self.assertNotIn("PRIVATE_UNTRUSTED_SYMBOL", json.dumps(receipt))

    def test_conflicting_symbol_aliases_remain_fail_closed(self):
        row = byma_record("PRIVATE_FIRST_SYMBOL")
        row["symbol"] = "PRIVATE_SECOND_SYMBOL"
        self.byma["sources"][0]["records"] = [row]
        self.write()
        receipt = self.inspect()
        self.assertEqual(receipt["counts"]["byma_key_valid"], 0)
        self.assertEqual(receipt["reject_counts"]["SYMBOL_CONFLICT"], 1)
        self.assertFalse(receipt["source_contract_ready"])
        self.assertNotIn("PRIVATE_FIRST_SYMBOL", json.dumps(receipt))
        self.assertNotIn("PRIVATE_SECOND_SYMBOL", json.dumps(receipt))

    def test_catalog_duplicate_and_absent_identity_are_counted_without_symbols(self):
        self.catalog["instruments"].extend([
            {"canonical_identity": canonical()},
            {"canonical_identity": None},
            {"canonical_identity": canonical("PRIVATE_SECOND_SYMBOL")},
        ])
        self.write()
        result = self.inspect()
        self.assertEqual(result["counts"]["catalog_rows"], 4)
        self.assertEqual(result["counts"]["catalog_exact_keys"], 2)
        self.assertEqual(result["counts"]["catalog_duplicate_identity"], 1)
        self.assertEqual(result["counts"]["catalog_missing_identity"], 1)
        self.assertNotIn("PRIVATE_SECOND_SYMBOL", json.dumps(result))

    def test_catalog_unknown_component_never_counts_as_exact(self):
        invalid = []
        for offset in range(5):
            key = canonical()
            key[offset] = "UNKNOWN"
            invalid.append({"canonical_identity": key})
        self.catalog["instruments"].extend(invalid)
        self.write()
        result = self.inspect()
        self.assertEqual(result["counts"]["catalog_rows"], 6)
        self.assertEqual(result["counts"]["catalog_exact_keys"], 1)
        self.assertEqual(result["counts"]["catalog_missing_identity"], 5)
        self.assertEqual(result["counts"]["byma_exact_rows"], 1)
        self.assertEqual(result["market_readiness"], "NO_VERIFICADO")
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_native_timestamp_naive_is_not_provider_aware(self):
        self.byma["sources"][0]["records"][0]["timestamp"] = "2026-11-02T18:00:00"
        self.write()
        result = self.inspect()
        self.assertEqual(result["counts"]["byma_native_clock_present"], 1)
        self.assertEqual(result["counts"]["byma_native_clock_aware"], 0)
        self.assertFalse(result["causal_pit_verified"])

    def test_symlink_parent_or_final_and_hardlinks_rejected(self):
        self.write()
        link_file = self.folder / "link.json"
        link_file.symlink_to(self.byma_path)
        result = probe.inspect_files(self.catalog_path, link_file, now=self.now)
        self.assertEqual(result["diagnostic_status"], "NO_VERIFICADO")
        self.assertEqual(result["reason"], "READ_UNAVAILABLE")
        link_parent = self.folder / "link-folder"
        link_parent.symlink_to(self.folder, target_is_directory=True)
        result = probe.inspect_files(link_parent / "catalog.json", self.byma_path, now=self.now)
        self.assertIn(result["reason"], {"READ_UNAVAILABLE", "PATH_UNSAFE"})
        hardlink = self.folder / "hardlink.json"
        os.link(self.byma_path, hardlink)
        result = probe.inspect_files(self.catalog_path, self.byma_path, now=self.now)
        self.assertEqual(result["reason"], "FILE_UNSAFE")

    def test_same_size_mtime_but_changed_ctime_is_rejected(self):
        self.write()
        real_fstat = os.fstat
        calls = [0]

        def changed_ctime(fd):
            original = real_fstat(fd)
            calls[0] += 1
            if calls[0] != 2:
                return original
            names = ("st_mode", "st_nlink", "st_size", "st_dev", "st_ino",
                     "st_mtime_ns", "st_ctime_ns")
            fields = {name: getattr(original, name) for name in names}
            fields["st_ctime_ns"] += 1
            return SimpleNamespace(**fields)

        with mock.patch.object(probe.os, "fstat", side_effect=changed_ctime):
            with self.assertRaises(probe.DiagnosticRejected) as raised:
                probe.safe_bytes(self.catalog_path, probe.MAX_CATALOG_BYTES)
        self.assertEqual(str(raised.exception), "FILE_CHANGED")
        self.assertEqual(calls[0], 2)

    def test_bad_schema_invalid_json_future_clock_and_oversize(self):
        self.catalog["schema"] = "PRIVATE_OTHER_SCHEMA"
        self.write()
        self.assertEqual(self.inspect()["reason"], "CATALOG_SCHEMA_INVALID")
        self.catalog["schema"] = probe.CATALOG_SCHEMA
        self.write()
        self.byma_path.write_text("{invalid", encoding="utf-8")
        self.assertEqual(self.inspect()["reason"], "JSON_INVALID")
        self.write()
        future = (self.now + timedelta(minutes=15)).timestamp()
        os.utime(self.byma_path, (future, future))
        self.assertEqual(self.inspect()["reason"], "CLOCK_INVALID")
        self.write()
        with self.byma_path.open("ab") as stream:
            stream.truncate(probe.MAX_BYMA_BYTES + 1)
        self.assertEqual(self.inspect()["reason"], "FILE_SIZE_INVALID")

    def test_missing_file_is_never_green(self):
        result = probe.inspect_files(self.catalog_path, self.byma_path, now=self.now)
        self.assertEqual(result["diagnostic_status"], "NO_VERIFICADO")
        self.assertEqual(result["reason"], "FILE_MISSING")

    def test_byma_producer_schema_and_exact_single_label_fail_closed(self):
        good = {"schema": probe.BYMA_SCHEMA,
                "sources": [{"source": "BYMA", "records": [byma_record()]}]}
        bad = [
            dict(good, schema="PRIVATE_OTHER_SCHEMA"),
            {"schema": probe.BYMA_SCHEMA, "sources": []},
            {"schema": probe.BYMA_SCHEMA,
             "sources": [{"source": "BYMA_PUBLIC", "records": [byma_record()]}]},
            {"schema": probe.BYMA_SCHEMA,
             "sources": [{"source": "byma", "records": [byma_record()]}]},
            {"schema": probe.BYMA_SCHEMA,
             "sources": [{"source": "BYMA", "records": []}]},
            {"schema": probe.BYMA_SCHEMA,
             "sources": [good["sources"][0], good["sources"][0]]},
        ]
        for number, payload in enumerate(bad):
            with self.subTest(case=number):
                self.write(byma=payload)
                report = self.inspect()
                self.assertEqual(report["diagnostic_status"], "NO_VERIFICADO")
                self.assertEqual(report["reason"], "BYMA_SHAPE_INVALID")
                self.assertEqual(report["counts"]["byma_rows"], 0)
                self.assertEqual(report["market_readiness"], "NO_VERIFICADO")
                self.assertNotIn(PRIVATE, json.dumps(report))
        self.write(byma=good)
        positive = self.inspect()
        self.assertEqual(positive["diagnostic_status"], "JOIN_COUNTS_ONLY")
        self.assertEqual(positive["counts"]["byma_exact_rows"], 1)

    def test_address_space_limit_is_enforced_in_real_offhost_child(self):
        # An isolated runner process gets both RLIMIT_AS hard and soft caps;
        # never alter the parent process address space budget.
        child = "\n".join([
            "import importlib.util, resource",
            f"spec=importlib.util.spec_from_file_location('a410_guard', {str(PATH)!r})",
            "m=importlib.util.module_from_spec(spec)",
            "spec.loader.exec_module(m)",
            "before=resource.getrlimit(resource.RLIMIT_AS)",
            "m._lock_address_space_limit()",
            "after=resource.getrlimit(resource.RLIMIT_AS)",
            "assert 0 < after[0] == after[1] <= m.MAX_ADDRESS_SPACE_BYTES",
            "assert before[0] == resource.RLIM_INFINITY or after[0] <= before[0]",
            "assert before[1] == resource.RLIM_INFINITY or after[1] <= before[1]",
            "print('CAP_ENFORCED')",
        ])
        completed = subprocess.run([sys.executable, "-B", "-c", child],
                                   capture_output=True, text=True, timeout=10,
                                   check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "CAP_ENFORCED")

    def test_memory_enforcement_failure_never_reads_host_json(self):
        output = StringIO()
        with (
            mock.patch.object(probe, "_lock_address_space_limit",
                              side_effect=probe.DiagnosticRejected(
                                  "MEMORY_LIMIT_UNAVAILABLE")),
            mock.patch.object(probe, "inspect_files",
                              side_effect=AssertionError("host read forbidden")),
        ):
            with redirect_stdout(output):
                status = probe.main(["host-probe"])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(report["diagnostic_status"], "NO_VERIFICADO")
        self.assertEqual(report["reason"], "MEMORY_LIMIT_UNAVAILABLE")
        self.assertEqual(report["market_readiness"], "NO_VERIFICADO")
        self.assertFalse(report["source_contract_ready"])
        self.assertNotIn(PRIVATE, json.dumps(report))

    def test_host_action_stays_on_main_and_never_on_pr(self):
        workflow = (PATH.parents[1] / ".github/workflows/rc6-a410-byma-catalog-join-readonly.yml").read_text(
            encoding="utf-8")
        self.assertIn("if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'", workflow)
        self.assertIn("'sudo -n timeout 24 python3 -B - host-probe'", workflow)
        self.assertIn("retention-days: 14", workflow)
        self.assertIn("market_readiness", workflow)
        self.assertNotIn("workflow_dispatch:\n    inputs:", workflow)


if __name__ == "__main__":
    unittest.main()
