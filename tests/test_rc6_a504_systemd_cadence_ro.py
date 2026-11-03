"""A504 bounded/offhost tests; never invoke systemctl, journalctl, SSH or DB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rc6_a504_systemd_cadence_ro.py"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/rc6-a420-primary-source-layout-readonly.yml"
spec = importlib.util.spec_from_file_location("a504", SCRIPT)
assert spec and spec.loader
a504 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a504)
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def art(y=2026, m=11, d=3, h=11, minute=0, second=0):
    return datetime(y, m, d, h, minute, second, tzinfo=ART).astimezone(timezone.utc)


def host(*, last_trigger_us=9_990_000_000, last_exit_us=9_995_000_000,
         service_result="success", active="active"):
    return {"query": "READ_ONLY", "timer_active": active, "service_result": service_result,
            "last_trigger_us": last_trigger_us, "last_exit_us": last_exit_us}


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
        r = a504.classify("SOURCE_CYCLE", now, (), host(last_trigger_us=9_000_000_000),
                          boot_us=12_000_000_000, source="UNKNOWN")
        self.assertEqual(r["classification"], "SCHEDULE_SILENT")

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

    def test_eod_result_cannot_certify_source_content(self):
        now = art(h=19)
        r = a504.classify("EOD_LOCAL", now, sample_two(now, "EOD_LOCAL"),
                          host(), boot_us=10_000_000_000)
        self.assertEqual(r["classification"], "RECENT_INVOCATIONS_OBSERVED")
        self.assertEqual(r["source"], "NOT_APPLICABLE")
        self.assertFalse(r["full_day_cadence_certified"])


class A504MetadataTests(unittest.TestCase):
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

    def test_source_only_fixed_small_file_and_stale_class(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"state.json"
            path.write_text(json.dumps({"schema":"rc6.source-cycle-dispatch.v1",
                                        "session_date":"2026-11-03",
                                        "last_iol_success_at":"2026-11-03T13:58:00Z"}))
            with patch.object(a504, "SOURCE_STATE", path):
                self.assertEqual(a504._bounded_state_source(art(h=11)), "FRESH")
                self.assertEqual(a504._bounded_state_source(art(h=12)), "STALE_OR_MISSING")

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
                                  "last_two_mature_distinct":False,"source":"UNKNOWN",
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
                                  "last_two_mature_distinct":False,"source":"UNKNOWN",
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


if __name__ == "__main__":
    unittest.main()
