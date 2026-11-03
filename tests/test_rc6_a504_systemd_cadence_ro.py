"""A504 bounded/offhost tests; never invoke systemctl, journalctl, SSH or DB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rc6_a504_systemd_cadence_ro.py"
SOURCE_LAYOUT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rc6_a420_primary_source_layout_ro.py"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/rc6-a420-primary-source-layout-readonly.yml"
spec = importlib.util.spec_from_file_location("a504", SCRIPT)
assert spec and spec.loader
a504 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a504)
source_spec = importlib.util.spec_from_file_location("a420_source_layout", SOURCE_LAYOUT_SCRIPT)
assert source_spec and source_spec.loader
a420 = importlib.util.module_from_spec(source_spec)
source_spec.loader.exec_module(a420)
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def art(y=2026, m=11, d=3, h=11, minute=0, second=0):
    return datetime(y, m, d, h, minute, second, tzinfo=ART).astimezone(timezone.utc)


def host(*, last_trigger_us=9_990_000_000, last_exit_us=9_995_000_000,
         service_result="success", active="active",
         schedule_parity_verified=False, clock_compatible=False):
    return {"query": "READ_ONLY", "timer_active": active, "service_result": service_result,
            "last_trigger_us": last_trigger_us, "last_exit_us": last_exit_us,
            "schedule_parity_verified": schedule_parity_verified,
            "clock_compatible": clock_compatible}


def sample_two(now, role="SOURCE_CYCLE"):
    mature = [s for s in a504.nominal_slots(role, now.astimezone(ART).date())
              if now - timedelta(minutes=a504.LOOKBACK_MINUTES) <= s <=
              now - timedelta(seconds=a504.MATURE_GRACE_SECONDS)]
    return (mature[-2] + timedelta(seconds=5), mature[-1] + timedelta(seconds=5))


class A504NominalTests(unittest.TestCase):
    def test_art_schedule_regular_has_450_nominal_one_minute_slots(self):
        slots = a504.nominal_slots("SOURCE_CYCLE", art().astimezone(ART).date())
        self.assertEqual(len(slots), 450)
        self.assertEqual(slots[0], art(h=10, minute=30))
        self.assertEqual(slots[-1], art(h=17, minute=59))
        self.assertNotIn(art(h=18), slots)

    def test_t1_after_17_is_source_cycle_not_global_public_republication(self):
        slots = a504.nominal_slots("SOURCE_CYCLE", art().astimezone(ART).date())
        self.assertIn(art(h=17, minute=45), slots)
        self.assertNotIn(art(h=18, minute=7), slots)

    def test_snapshot_com19031_skips_old_1708_and_retries_until_2220(self):
        slots = a504.nominal_slots("POSTCLOSE_SNAPSHOT", art().astimezone(ART).date())
        self.assertNotIn(art(h=17, minute=8), slots)
        self.assertIn(art(h=18, minute=8), slots)
        self.assertIn(art(h=22, minute=20), slots)
        self.assertNotIn(art(h=22, minute=50), slots)

    def test_weekend_and_predue_are_not_due(self):
        saturday = art(d=7, h=12)
        self.assertEqual(a504.nominal_slots("SOURCE_CYCLE", saturday.astimezone(ART).date()), ())
        r = a504.classify("SOURCE_CYCLE", art(h=10, minute=29), (), host(), boot_us=10_000_000_000)
        self.assertEqual(r["classification"], "NOT_DUE")


class A504ClassifyTests(unittest.TestCase):
    def test_recent_two_distinct_source_invocations_are_observations_only(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, sample_two(now), host(),
                          boot_us=10_000_000_000, source="FRESH")
        self.assertEqual(r["classification"], "RECENT_INVOCATIONS_OBSERVED")
        self.assertTrue(r["last_two_mature_distinct"])
        self.assertFalse(r["full_day_cadence_certified"])

    def test_missing_latest_slot_fails_closed_not_green(self):
        now = art(h=11, minute=5)
        old, new = sample_two(now)
        r = a504.classify("SOURCE_CYCLE", now, (old,), host(),
                          boot_us=10_000_000_000, source="FRESH")
        self.assertEqual(r["classification"], "NO_VERIFICADO")
        self.assertFalse(r["last_two_mature_distinct"])

    def test_same_slot_burst_cannot_impersonate_two_slots(self):
        now = art(h=11, minute=5)
        old, _ = sample_two(now)
        r = a504.classify("SOURCE_CYCLE", now,
                          (old, old + timedelta(seconds=1)), host(),
                          boot_us=10_000_000_000, source="FRESH")
        self.assertEqual(r["classification"], "NO_VERIFICADO")

    def test_current_timer_was_silent_after_multiple_due_slots(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, (),
                          host(last_trigger_us=9_000_000_000,
                               schedule_parity_verified=True, clock_compatible=True),
                          boot_us=12_000_000_000, source="UNKNOWN")
        self.assertEqual(r["classification"], "SCHEDULE_SILENT")

    def test_journal_unavailable_never_proves_schedule_silence(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, None,
                          host(last_trigger_us=9_000_000_000,
                               schedule_parity_verified=True, clock_compatible=True),
                          boot_us=12_000_000_000, source="UNKNOWN")
        self.assertEqual(r["classification"], "NO_VERIFICADO")
        self.assertFalse(r["journal_available"])

    def test_unverified_installed_calendar_never_proves_schedule_silence(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, (),
                          host(last_trigger_us=9_000_000_000,
                               schedule_parity_verified=False, clock_compatible=True),
                          boot_us=12_000_000_000, source="UNKNOWN")
        self.assertEqual(r["classification"], "NO_VERIFICADO")

    def test_unverified_clock_domain_never_proves_schedule_silence(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, (),
                          host(last_trigger_us=9_000_000_000,
                               schedule_parity_verified=True, clock_compatible=False),
                          boot_us=12_000_000_000, source="UNKNOWN")
        self.assertEqual(r["classification"], "NO_VERIFICADO")

    def test_recent_invocation_contradicts_stale_lasttrigger_and_fails_closed(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, sample_two(now),
                          host(last_trigger_us=9_000_000_000), boot_us=12_000_000_000,
                          source="UNKNOWN")
        self.assertEqual(r["classification"], "NO_VERIFICADO")

    def test_boot_shorter_than_lookback_cannot_prove_silence(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, (), host(last_trigger_us=1_000_000),
                          boot_us=2_000_000, source="UNKNOWN")
        self.assertEqual(r["classification"], "NO_VERIFICADO")

    def test_failed_recent_invocation_with_exit_after_trigger(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, sample_two(now),
                          host(service_result="exit-code"), boot_us=10_000_000_000,
                          source="STALE_OR_MISSING")
        self.assertEqual(r["classification"], "FAILED")

    def test_old_failure_cannot_be_assigned_to_recent_trigger(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, sample_two(now),
                          host(last_exit_us=8_000_000_000, service_result="exit-code"),
                          boot_us=10_000_000_000, source="STALE_OR_MISSING")
        self.assertEqual(r["classification"], "NO_SOURCE")

    def test_source_absent_after_recent_invocations_is_no_source_not_failed(self):
        now = art(h=11, minute=5)
        r = a504.classify("SOURCE_CYCLE", now, sample_two(now), host(),
                          boot_us=10_000_000_000, source="STALE_OR_MISSING")
        self.assertEqual(r["classification"], "NO_SOURCE")

    def test_late_t1_not_due_must_not_be_mislabeled_no_source(self):
        now = art(h=17, minute=45)
        r = a504.classify("SOURCE_CYCLE", now, sample_two(now), host(),
                          boot_us=10_000_000_000, source="STALE_OR_MISSING")
        self.assertEqual(r["classification"], "NO_VERIFICADO")

    def test_eod_result_cannot_certify_source_content(self):
        now = art(h=19)
        r = a504.classify("EOD_LOCAL", now, sample_two(now, "EOD_LOCAL"),
                          host(), boot_us=10_000_000_000)
        self.assertEqual(r["classification"], "RECENT_INVOCATIONS_OBSERVED")
        self.assertEqual(r["source"], "NOT_APPLICABLE")
        self.assertFalse(r["full_day_cadence_certified"])


class A504MetadataTests(unittest.TestCase):
    @staticmethod
    def _source_state(**changes):
        # Actual deployed producer contract, not an invented IOL-only clock.
        state = {"schema": "rc6.source-cycle-dispatch.v1", "session_date": "2026-11-03",
                 "last_public_success_at": "2026-11-03T13:59:30Z",
                 "last_evidence_success_at": "2026-11-03T13:57:00+00:00"}
        state.update(changes)
        return state

    def _inspect_source_state(self, state, at=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            with patch.object(a504, "SOURCE_STATE", path):
                return a504._bounded_state_source(at or art(h=11))

    def test_journal_query_excludes_message_and_bounds_data(self):
        now = art(h=11, minute=5)
        commands = []
        def runner(args, **kwargs):
            commands.append(args)
            sample = {"__REALTIME_TIMESTAMP": str(int(now.timestamp()*1_000_000)),
                      "_SYSTEMD_UNIT": "porota-iol-shadow-collector-rc6.service",
                      "_SYSTEMD_INVOCATION_ID": "a"*32}
            return subprocess.CompletedProcess(args, 0, json.dumps(sample)+"\n", "")
        ticks = a504.journal_invocations("porota-iol-shadow-collector-rc6.service", now,
                                         runner=runner)
        self.assertEqual(len(ticks), 1)
        joined = " ".join(commands[0])
        self.assertIn("--output-fields=__REALTIME_TIMESTAMP,_SYSTEMD_UNIT,_SYSTEMD_INVOCATION_ID", joined)
        self.assertNotIn("MESSAGE,", joined)
        self.assertNotIn("--follow", joined)

    def test_oversized_journal_fails_closed(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "x"*(a504.MAX_JOURNAL_BYTES+1), "")
        self.assertIsNone(a504.journal_invocations(
            "porota-iol-shadow-collector-rc6.service", art(), runner=runner))

    def test_source_real_stage_clocks_are_required_and_compatible_with_a420(self):
        state = self._source_state()
        self.assertEqual(a504.SOURCE_STATE_SCHEMA, a420.STATE_SCHEMA)
        self.assertEqual(tuple(key for key, _ in a504.SOURCE_STAGE_TTLS),
                         ("last_public_success_at", "last_evidence_success_at"))
        self.assertEqual(a504.SOURCE_STAGE_TTLS[0][1], 60)
        self.assertEqual(a504.SOURCE_STAGE_TTLS[1][1], 5 * 60)
        self.assertEqual(self._inspect_source_state(state), "FRESH")
        receipt = a420._state_receipt(json.dumps(state).encode(), art(h=11))
        self.assertEqual(receipt["state_schema"], "MATCHED")
        self.assertEqual(receipt["session"], "TODAY")
        self.assertEqual(receipt["public_last_success"], "WITHIN_5M")
        self.assertEqual(receipt["evidence_last_success"], "WITHIN_5M")
        # No A338 budget sidecar, provider request or Book is read here.
        self.assertEqual(self._inspect_source_state(state, art(h=12)), "STALE_OR_MISSING")

    def test_source_old_fabricated_iol_clock_does_not_override_missing_producer_stages(self):
        state = self._source_state(last_public_success_at=None,
                                   last_evidence_success_at=None,
                                   last_iol_success_at="2026-11-03T13:59:59Z")
        self.assertEqual(self._inspect_source_state(state), "STALE_OR_MISSING")

    def test_source_each_real_stage_missing_or_expired_fails_closed(self):
        for field, expired_stamp in (("last_public_success_at", "2026-11-03T13:59:00Z"),
                                     ("last_evidence_success_at", "2026-11-03T13:55:00Z")):
            with self.subTest(field=field):
                self.assertEqual(self._inspect_source_state(self._source_state(**{field: None})),
                                 "STALE_OR_MISSING")
                self.assertEqual(self._inspect_source_state(self._source_state(**{field: expired_stamp})),
                                 "STALE_OR_MISSING")

    def test_source_rejects_other_session_invalid_or_future_stage_clocks(self):
        self.assertEqual(self._inspect_source_state(self._source_state(session_date="2026-11-02")),
                         "STALE_OR_MISSING")
        for bad_stamp in ("not-a-clock", "2026-11-03T13:59:30", "2026-11-03T14:00:01Z"):
            with self.subTest(bad_stamp=bad_stamp):
                self.assertEqual(self._inspect_source_state(
                    self._source_state(last_public_success_at=bad_stamp)), "UNKNOWN")
        self.assertEqual(self._inspect_source_state(self._source_state(schema="wrong")), "UNKNOWN")

    def test_source_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"state.json"
            path.symlink_to(Path(folder)/"missing")
            with patch.object(a504, "SOURCE_STATE", path):
                self.assertEqual(a504._bounded_state_source(art()), "UNKNOWN")

    def test_sanitizer_rejects_arbitrary_host_fields_and_false_certification(self):
        data = a504.default_receipt()
        data["probe_status"] = "METADATA_ONLY"
        for role,_ in a504.ROLES:
            data["roles"].append({"role":role,"classification":"NO_VERIFICADO",
                                  "nominal_mature_slots":3,"observed_distinct_slots":0,
                                  "last_two_mature_distinct":False,"journal_available":True,
                                  "schedule_parity_verified":False,"clock_compatible":False,
                                  "source":"UNKNOWN",
                                  "timer":"active","service":"success",
                                  "full_day_cadence_certified":False})
        self.assertEqual(a504.sanitize_receipt(json.dumps(data).encode()),data)
        data["host_secrets"]="must not escape"
        self.assertEqual(a504.sanitize_receipt(json.dumps(data).encode())["roles"],[])
        del data["host_secrets"]
        data["roles"][0]["full_day_cadence_certified"]=True
        self.assertEqual(a504.sanitize_receipt(json.dumps(data).encode())["roles"],[])

    def test_sanitizer_rejects_forged_source_or_schedule_conclusions(self):
        data = a504.default_receipt()
        data["probe_status"] = "METADATA_ONLY"
        for role,_ in a504.ROLES:
            data["roles"].append({"role":role,"classification":"NO_VERIFICADO",
                                  "nominal_mature_slots":3,"observed_distinct_slots":0,
                                  "last_two_mature_distinct":False,"journal_available":True,
                                  "schedule_parity_verified":False,"clock_compatible":False,
                                  "source":"UNKNOWN",
                                  "timer":"active","service":"success",
                                  "full_day_cadence_certified":False})
        data["roles"][1]["classification"]="NO_SOURCE"
        self.assertEqual(a504.sanitize_receipt(json.dumps(data).encode())["roles"],[])
        data["roles"][1]["classification"]="NO_VERIFICADO"
        data["roles"][0]["classification"]="SCHEDULE_SILENT"
        data["roles"][0]["timer"]="inactive"
        self.assertEqual(a504.sanitize_receipt(json.dumps(data).encode())["roles"],[])

    def test_workflow_uses_existing_nonrecurring_host_job_and_pr_is_offhost(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("rc6_a504_systemd_cadence_ro.py", text)
        self.assertIn("github.event_name != 'pull_request'", text)
        self.assertNotIn("  schedule:", text)
        self.assertNotIn("PPI Watch", text)

    def test_failing_sanitizer_executes_exact_raw_cleanup_without_touching_other_artifacts(self):
        """Execute the workflow shell itself with mocked SSH and sanitizer only."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        body = workflow.split("      - name: A504 bounded systemd cadence metadata (no journal messages)\n", 1)[1]
        body = body.split("      - name: A504 publish only sanitized bounded cadence classifications\n", 1)[0]
        shell = textwrap.dedent(body.split("        run: |\n", 1)[1])
        self.assertIn("trap 'rm -f -- evidence/.a504-raw.json' EXIT", shell)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "evidence").mkdir()
            (root / "scripts").mkdir()
            (root / "bin").mkdir()
            (root / "scripts/rc6_a504_systemd_cadence_ro.py").write_text("# mock stdin\n")
            (root / "evidence/.a504-raw.json").write_text("old untrusted stdout")
            other = root / "evidence/.raw.json"
            other.write_text("A420 raw artifact untouched")
            receipt = root / "evidence/a504-cadence.json"
            receipt.write_text("validated receipt untouched")
            ssh_mock = root / "bin/ssh"
            ssh_mock.write_text("#!/bin/sh\nprintf 'untrusted simulated stdout\\n'\n")
            ssh_mock.chmod(0o700)
            sanitizer_mock = root / "bin/python3"
            sanitizer_mock.write_text("#!/bin/sh\nexit 49 # force sanitizer failure before its output\n")
            sanitizer_mock.chmod(0o700)
            env = dict(os.environ)
            env.update({"PATH": str(root / "bin") + os.pathsep + env.get("PATH", ""),
                        "DO_USER": "mock-user", "DO_HOST": "mock-host"})
            completed = subprocess.run(["bash", "-c", shell], cwd=root, env=env,
                                       capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(completed.returncode, 49, completed.stderr)
            self.assertFalse((root / "evidence/.a504-raw.json").exists())
            self.assertEqual(other.read_text(), "A420 raw artifact untouched")
            self.assertEqual(receipt.read_text(), "validated receipt untouched")


if __name__ == "__main__":
    unittest.main()
