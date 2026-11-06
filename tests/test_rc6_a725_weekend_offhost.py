"""A725: synthetic receipts only; proves fail-closed and no readiness promotion."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts import rc6_a725_weekend_offhost as a725


class WeekendEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "metadata").mkdir()
        (self.root / "logs").mkdir()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        for key, (rid, sha, schema) in a725.PINS.items():
            self.put(self.root / "metadata" / f"{rid}.json", {
                "id": rid, "head_sha": sha, "event": "push" if key == "r2" else "workflow_dispatch",
                "status": "completed", "conclusion": "failure" if key == "r2" else "success",
                "untrusted_private": "DO_NOT_COPY_ME"})
            if schema:
                row = {"schema": schema, "provider_gets": 0, "read_only": True,
                       "orders_called": False,
                       "state": "DIAGNOSTIC_ONLY" if key == "a724" else "READ_ONLY_OBSERVED"}
                if key == "a627":
                    row.update(source_status="VERIFIED_API", due_slots=21,
                               source_scheduler_finding="P1_SCHEDULE_SILENT",
                               order_routes_called=False)
                if key == "a655":
                    row["status_coverage"] = {fam: {"active_gap": n}
                                              for fam, n in {"BONOS": 8,
                                                  "OBLIGACIONES": 52, "OPCIONES": 576,
                                                  "FUTUROS": 170, "CAUCIONES": 5,
                                                  "FCI": 21}.items()}
                    row["catalog_active_gap_total"] = 832
                if key == "a668":
                    row["target_by_family"] = {"BONOS": 4, "OBLIGACIONES": 52, "FCI": 21}
                    row["target_total"] = 77
                if key == "a617":
                    row.update(scanner_receipt={"state": "ABSENT"},
                               native_book_journal={"state": "ABSENT"},
                               effective_ppi_lease="NO_VERIFICADO")
                if key == "a724":
                    row.update(marker={"relation": "R1_LAST_GO"},
                               barrier={"status": "IDLE"},
                               cold={"successor_predicate": "NO_VERIFICADO"},
                               scheduler={"receipt_status": "SUSPENDED"},
                               unreferenced_total=2, mutations=0,
                               runtime_running_verified=False)
                self.put(self.artifacts / key / "sanitized.json", row)
        cleanup = {"schema": "POROTA_DEPLOY_FAILURE_CLEANUP_V1",
                   "candidate_sha": "a16a31c7bc4634362870c731273e084eea9d357d",
                   "status": "GREEN", "staging_removed": True, "removed_tags": [],
                   "space_recovered": 293736448,
                   "retained_reasons": {"candidate1": "NONCANDIDATE_ALIAS_OR_DIGEST",
                                        "candidate2": "NONCANDIDATE_ALIAS_OR_DIGEST"}}
        self.log = self.root / "logs" / "r2.log"
        self.log.write_text(
            "2026-11-06T17:18:42Z ValueError: COLD_ARCHIVED_RECOVERY_NOT_PROVEN\n"
            "2026-11-06T17:18:51Z ValueError: SCHEDULER_STOPPED_CLOSED_FLAT_RED\n"
            "2026-11-06T17:18:54Z " + json.dumps(cleanup) + "\n")

    @staticmethod
    def put(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_all_six_pinned_receipts_synthetic_and_ten_family_hold(self):
        result = a725.consolidate(self.root)
        self.assertEqual(result["artifact_integrity"], "GREEN")
        self.assertEqual(result["state"], "OFFHOST_RECONCILED")
        self.assertEqual(set(result["sources"]), set(a725.PINS))
        self.assertEqual(len(result["families"]), 10)
        self.assertEqual({x["family"] for x in result["families"]}, set(a725.FAMILIES))
        self.assertEqual(sum(x["active_contract_gap"] or 0 for x in result["families"]), 832)
        self.assertEqual(result["families"][0]["active_contract_gap"], None)
        self.assertEqual(result["ppi"]["lease_effective"], "NO_VERIFICADO")
        self.assertEqual(result["ppi"]["native_book_journal"], "ABSENT")
        self.assertEqual(result["schedules"]["due_slots"], 21)
        self.assertEqual(result["r2_failure"]["cleanup"]["retained_tags"], 2)
        self.assertFalse(result["paper_ready_certified"])
        self.assertFalse(result["causal_pit_certified"])
        self.assertFalse(any(x["paper_readiness"] != "NO_VERIFICADO" for x in result["families"]))
        self.assertNotIn("DO_NOT_COPY_ME", json.dumps(result))

    def test_missing_artifact_fails_closed_preserving_other_evidence(self):
        (self.artifacts / "a655" / "sanitized.json").unlink()
        result = a725.consolidate(self.root)
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertEqual(result["sources"]["a655"]["source_integrity"], "NO_VERIFICADO")
        self.assertEqual(result["sources"]["a617"]["source_integrity"], "GREEN")
        self.assertTrue(all(x["active_contract_gap"] is None for x in result["families"]))

    def test_provider_read_or_sha_drift_fails_closed(self):
        artifact = self.artifacts / "a668" / "sanitized.json"
        payload = json.loads(artifact.read_text())
        payload["provider_gets"] = 1
        self.put(artifact, payload)
        result = a725.consolidate(self.root)
        self.assertEqual(result["sources"]["a668"]["reason"],
                         "NONZERO_PROVIDER_OR_NON_READONLY")
        self.assertEqual(result["artifact_integrity"], "NO_VERIFICADO")
        meta = self.root / "metadata" / "42249258042.json"
        bad = json.loads(meta.read_text())
        bad["head_sha"] = "0" * 40
        self.put(meta, bad)
        self.assertEqual(a725.consolidate(self.root)["sources"]["a617"]["reason"],
                         "RUN_METADATA_MISMATCH")

    def test_all_nested_diagnostic_paths_reject_secret_strings_and_scalars(self):
        secret = "SECRET_PRIVATE_TOKEN_DO_NOT_LEAK"
        paths = (
            ("a627", ("source_scheduler_finding",), secret),
            ("a617", ("scanner_receipt",), "oops"),
            ("a617", ("scanner_receipt", "state"), secret),
            ("a617", ("native_book_journal",), "oops"),
            ("a617", ("native_book_journal", "state"), secret),
            ("a724", ("marker",), "oops"),
            ("a724", ("marker", "relation"), secret),
            ("a724", ("barrier", "status"), secret),
            ("a724", ("cold", "successor_predicate"), secret),
            ("a724", ("scheduler", "receipt_status"), secret),
            ("a724", ("unreferenced_total",), secret),
        )
        for key, fields, malicious in paths:
            with self.subTest(key=key, fields=fields):
                path = self.artifacts / key / "sanitized.json"
                original = json.loads(path.read_text())
                tainted = json.loads(path.read_text())
                node = tainted
                for part in fields[:-1]:
                    node = node[part]
                node[fields[-1]] = malicious
                self.put(path, tainted)
                result = a725.consolidate(self.root)
                self.assertEqual(result["sources"][key]["source_integrity"], "NO_VERIFICADO")
                self.assertEqual(result["artifact_integrity"], "NO_VERIFICADO")
                self.assertNotIn(secret, json.dumps(result))
                self.assertNotIn("oops", json.dumps(result))
                self.put(path, original)

    def test_rejected_duplicate_json_and_unbounded_input(self):
        a617 = self.artifacts / "a617" / "sanitized.json"
        a617.write_text('{"schema":"x","schema":"y"}')
        self.assertEqual(a725.consolidate(self.root)["sources"]["a617"]["reason"],
                         "DUPLICATE_JSON_KEY")
        a617.write_text('x' * (a725.MAX_ARTIFACT + 1))
        self.assertEqual(a725.consolidate(self.root)["sources"]["a617"]["reason"],
                         "INPUT_ABSENT_OR_UNSAFE")

    def test_r2_log_wrong_candidate_never_proves_cleanup(self):
        self.log.write_text(self.log.read_text().replace(
            "a16a31c7bc4634362870c731273e084eea9d357d", "a" * 40))
        result = a725.consolidate(self.root)
        self.assertEqual(result["r2_failure"]["state"], "NO_VERIFICADO")
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertIn("r2_log:R2_CLEANUP_LOG_MISSING", result["holds"])

    def test_workflow_is_main_manual_no_runtime_credentials(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/rc6-a725-weekend-offhost.yml").read_text()
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("  actions: read", workflow)
        for forbidden in ("secrets.", "sudo ", "ssh ", "docker ", "ppi-client",
                          "rc6_history_fresh_probe", "github.event_name == 'schedule'"):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
