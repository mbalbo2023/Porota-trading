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
                    self.put(self.artifacts / key / "a617_schedule_gate.json", {
                        "schema": "rc6.a657.a617-schedule-gate.v1", "read_only": True,
                        "gate": "MANUAL_READ_ONLY_FALLBACK", "active": True,
                        "scheduled_slot_art": None, "delay_seconds": None,
                        "observed_at_art": "2026-11-06T11:30:01-03:00",
                        "host_probe_state": "NOT_ATTESTED_BY_GATE",
                        "provider_gets_by_gate": 0,
                        "order_routes_called_by_gate": False,
                        "ppi_watch_touched_by_gate": False})
                if key == "a724":
                    row.update(marker={"relation": "R1_LAST_GO"},
                               barrier={"status": "IDLE"},
                               cold={"successor_predicate": "NO_VERIFICADO"},
                               scheduler={"receipt_status": "SUSPENDED"},
                               unreferenced_total=2, mutations=0,
                               runtime_running_verified=False)
                filename = "a617_ppi_origin_selector.json" if key == "a617" else "sanitized.json"
                self.put(self.artifacts / key / filename, row)
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
        self.assertEqual(len(result["sources"]["a617"]["receipt_sha256"]), 64)
        self.assertEqual(len(result["sources"]["a617"]["a617_schedule_gate_sha256"]), 64)
        self.assertEqual(result["schedules"]["due_slots"], 21)
        self.assertEqual(result["r2_failure"]["cleanup"]["retained_tags"], 2)
        self.assertEqual(result["r2_log_integrity"], "GREEN")
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
                filename = "a617_ppi_origin_selector.json" if key == "a617" else "sanitized.json"
                path = self.artifacts / key / filename
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
        a617 = self.artifacts / "a617" / "a617_ppi_origin_selector.json"
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
        self.assertEqual(result["r2_log_integrity"], "NO_VERIFICADO")
        self.assertEqual(result["artifact_integrity"], "GREEN")
        self.assertEqual(result["state"], "OFFHOST_RECONCILED_PARTIAL")
        self.assertEqual(result["r2_failure"]["cleanup"], "NO_VERIFICADO")
        self.assertFalse(result["paper_ready_certified"])

    def test_a617_exact_two_json_files_are_required(self):
        primary = self.artifacts / "a617" / "a617_ppi_origin_selector.json"
        companion = self.artifacts / "a617" / "a617_schedule_gate.json"
        self.assertTrue(primary.is_file() and companion.is_file())
        result = a725.consolidate(self.root)
        self.assertEqual(result["sources"]["a617"]["source_integrity"], "GREEN")
        companion.unlink()
        result = a725.consolidate(self.root)
        self.assertEqual(result["sources"]["a617"]["reason"], "ARTIFACT_COUNT_OR_PATH")
        self.put(companion, {"schema": "x"})
        self.put(self.artifacts / "a617" / "injected_extra.json", {"secret": "LEAK"})
        result = a725.consolidate(self.root)
        self.assertEqual(result["sources"]["a617"]["reason"], "ARTIFACT_COUNT_OR_PATH")
        self.assertNotIn("LEAK", json.dumps(result))

    def test_a617_companion_requires_gate_and_zero_write_contract(self):
        companion = self.artifacts / "a617" / "a617_schedule_gate.json"
        original = json.loads(companion.read_text())
        malicious = "SECRET_PRIVATE_TOKEN_DO_NOT_LEAK"
        cases = (
            ("gate", malicious), ("active", "oops"),
            ("host_probe_state", malicious), ("provider_gets_by_gate", True),
            ("provider_gets_by_gate", 1), ("order_routes_called_by_gate", True),
            ("ppi_watch_touched_by_gate", True), ("read_only", False),
            ("observed_at_art", malicious),
            ("observed_at_art", "2026-11-05T11:30:01-03:00"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                changed = dict(original)
                changed[field] = value
                self.put(companion, changed)
                result = a725.consolidate(self.root)
                self.assertEqual(result["sources"]["a617"]["source_integrity"], "NO_VERIFICADO")
                self.assertEqual(result["artifact_integrity"], "NO_VERIFICADO")
                self.assertFalse(result["paper_ready_certified"])
                self.assertNotIn(malicious, json.dumps(result))
                self.assertNotIn("oops", json.dumps(result))
        companion.write_text("[]")
        self.assertEqual(a725.consolidate(self.root)["sources"]["a617"]["reason"],
                         "A617_COMPANION_GATE_UNTRUSTED")

    def test_r2_log_missing_degrades_without_green_root_cause(self):
        self.log.unlink()
        result = a725.consolidate(self.root)
        self.assertEqual(result["artifact_integrity"], "GREEN")
        self.assertEqual(result["state"], "OFFHOST_RECONCILED_PARTIAL")
        self.assertEqual(result["r2_log_integrity"], "NO_VERIFICADO")
        self.assertEqual(result["r2_failure"]["run_conclusion"], "failure")
        self.assertEqual(result["r2_failure"]["root_cause"], "NO_VERIFICADO")
        self.assertEqual(result["r2_failure"]["cleanup"], "NO_VERIFICADO")
        self.assertFalse(result["paper_ready_certified"])

    def test_r2_run_metadata_missing_remains_global_no_verificado(self):
        (self.root / "metadata" / "42260034260.json").unlink()
        result = a725.consolidate(self.root)
        self.assertEqual(result["artifact_integrity"], "NO_VERIFICADO")
        self.assertEqual(result["state"], "NO_VERIFICADO")
        self.assertEqual(result["sources"]["r2"]["source_integrity"], "NO_VERIFICADO")

    def test_workflow_is_main_manual_no_runtime_credentials(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/rc6-a725-weekend-offhost.yml").read_text()
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("gh run view 42260034260", workflow)
        self.assertIn("--job 128037233727 --log", workflow)
        self.assertIn("  actions: read", workflow)
        for forbidden in ("secrets.", "sudo ", "ssh ", "docker ", "ppi-client",
                          "rc6_history_fresh_probe", "github.event_name == 'schedule'"):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
