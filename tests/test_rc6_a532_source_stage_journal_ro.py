"""A532 offhost safety and contract tests; never invoke journalctl, SSH or providers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import unittest

from scripts import rc6_a532_source_stage_journal_ro as a532


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-a420-primary-source-layout-readonly.yml"
NOW = datetime(2026, 11, 3, 16, 20, 0, tzinfo=timezone.utc)


def marker(status="DEGRADED", stages=None, *, reason="PAPER_OBSERVE_ONLY",
           orders=0, event_time=None, unit=a532.UNIT):
    stage_values = ({"iol": "COLLECTOR_NOT_COMPLETED", "public": "FAILED",
                     "evidence": "OUTPUT_NOT_REPUBLISHED"} if stages is None else stages)
    result = {"status": status, "reason": reason, "stages": stage_values,
              "real_orders_sent": orders}
    when = event_time or (NOW - timedelta(seconds=90))
    record = {"__REALTIME_TIMESTAMP": str(int(when.timestamp() * 1_000_000)),
              "_SYSTEMD_UNIT": unit,
              "MESSAGE": a532.MARKER + json.dumps(result, separators=(",", ":"))}
    return (json.dumps(record, separators=(",", ":")) + "\n").encode()


class A532OffhostTests(unittest.TestCase):
    def test_fixed_journal_command_is_bounded_and_never_calls_provider(self):
        args = a532.journal_command(NOW)
        self.assertEqual(args[0], "/usr/bin/journalctl")
        self.assertIn("--unit", args)
        self.assertEqual(args[args.index("--unit") + 1], a532.UNIT)
        self.assertIn("--grep=^RC6_SOURCE_CYCLE_DISPATCH=", args)
        self.assertIn("--output-fields=__REALTIME_TIMESTAMP,_SYSTEMD_UNIT,MESSAGE", args)
        self.assertIn("--lines=80", args)
        self.assertIn("--since=2026-11-03 15:35:00 UTC", args)
        self.assertIn("--until=2026-11-03 16:20:00 UTC", args)
        self.assertNotIn("--follow", args)
        self.assertNotIn("--since=yesterday", args)
        joined = " ".join(args).lower()
        for forbidden in ("ppi", "credential", "secret", "sqlite", "curl", "systemctl start"):
            self.assertNotIn(forbidden, joined)

    def test_valid_degraded_stage_marker_reports_only_fixed_status_enums(self):
        result = a532.summarize_journal(marker(), now=NOW)
        self.assertEqual(result["marker"], "OBSERVED_UNATTESTED")
        self.assertEqual(result["reported_status"], "DEGRADED")
        self.assertEqual(result["reported_reason"], "PAPER_OBSERVE_ONLY")
        self.assertEqual(result["stage_iol"], "COLLECTOR_NOT_COMPLETED")
        self.assertEqual(result["stage_public"], "FAILED")
        self.assertEqual(result["stage_evidence"], "OUTPUT_NOT_REPUBLISHED")
        self.assertEqual(result["marker_age"], "WITHIN_2M")
        self.assertFalse(result["paper_ready"])
        self.assertFalse(result["cadence_verified"])
        self.assertFalse(result["ppi_book_verified"])
        self.assertFalse(result["raw_journal_exported"])
        self.assertEqual(result["journal_message_access_scope"], "HOST_ONLY_IF_AVAILABLE")
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["real_orders_sent_by_action"], 0)
        self.assertEqual(a532.sanitize_receipt(json.dumps(result).encode()), result)

    def test_complete_marker_does_not_certify_provider_or_fill(self):
        raw = marker("COMPLETE", {"iol": "OK", "public": "OK", "evidence": "OK"},
                     event_time=NOW - timedelta(minutes=8))
        result = a532.summarize_journal(raw, now=NOW)
        self.assertEqual(result["reported_status"], "COMPLETE")
        self.assertEqual(result["marker_age"], "WITHIN_10M")
        self.assertFalse(result["cadence_verified"])
        self.assertFalse(result["paper_ready"])
        self.assertFalse(result["ppi_book_verified"])

    def test_latest_valid_marker_wins_without_raw_data_or_timestamps(self):
        past = marker("COMPLETE", {"iol": "OK", "public": "OK", "evidence": "OK"},
                      event_time=NOW - timedelta(minutes=20))
        current = marker(event_time=NOW - timedelta(minutes=1))
        report = a532.summarize_journal(current + past, now=NOW)
        self.assertEqual(report["reported_status"], "DEGRADED")
        self.assertNotIn("2026-11-03T", json.dumps(report))
        self.assertNotIn("RC6_SOURCE_CYCLE_DISPATCH=", json.dumps(report))
        self.assertEqual(report["marker_age"], "WITHIN_2M")

    def test_blocked_reason_collapses_path_override_and_keeps_stages_unreported(self):
        sample = marker("BLOCKED", {}, reason="SOURCE_CYCLE_PATH_OVERRIDE_DIVERGED:POROTA_OBSERVER_DB")
        report = a532.summarize_journal(sample, now=NOW)
        self.assertEqual(report["reported_reason"], "SOURCE_CYCLE_PATH_OVERRIDE_DIVERGED")
        self.assertEqual(report["reported_status"], "BLOCKED")
        self.assertEqual(report["stage_public"], "NOT_REPORTED")
        self.assertNotIn("POROTA_OBSERVER_DB", json.dumps(report))
        self.assertEqual(a532.sanitize_receipt(json.dumps(report).encode()), report)

    def test_wrong_unit_future_or_other_day_cannot_attest_state(self):
        wrong = marker(unit="other.service")
        future = marker(event_time=NOW + timedelta(seconds=1))
        previous = marker(event_time=NOW - timedelta(minutes=41))
        self.assertEqual(a532.summarize_journal(wrong, now=NOW), a532.default_receipt())
        self.assertEqual(a532.summarize_journal(future, now=NOW), a532.default_receipt())
        self.assertEqual(a532.summarize_journal(previous, now=NOW)["marker_age"], "WITHIN_45M")

    def test_only_valid_status_and_zero_provider_side_effects_enter_receipt(self):
        for raw in (
                marker("COMPLETE", {"public": "FAILED"}),
                marker("COMPLETE", {}),
                marker("DEGRADED", {}),
                marker("DEGRADED", {"iol": "OK", "public": "NOT_DUE_TTL"}),
                marker("DEGRADED", {"public": "REALLY_SUCCEEDED"}),
                marker("COMPLETE", {"public": "OK"}, orders=1),
                marker("COMPLETE", {"public": "OK"}, orders=False),
                marker("BLOCKED", {"public": "FAILED"}),
        ):
            with self.subTest(raw=raw[:30]):
                self.assertEqual(a532.summarize_journal(raw, now=NOW), a532.default_receipt())

    def test_hostile_journal_data_never_leaks_via_receipt(self):
        secret = "private-key-sentinel"
        raw = marker() + json.dumps({
            "__REALTIME_TIMESTAMP": str(int(NOW.timestamp()*1_000_000)),
            "_SYSTEMD_UNIT": a532.UNIT,
            "MESSAGE": a532.MARKER + json.dumps({"status": "DEGRADED",
                "reason": secret, "stages": {"public": "FAILED"}, "real_orders_sent": 0})
        }).encode() + b"\n"
        result = a532.summarize_journal(raw, now=NOW)
        self.assertEqual(result["reported_reason"], "OTHER_OR_UNVERIFIED")
        self.assertNotIn(secret, json.dumps(result))

    def test_rejects_duplicate_json_injected_timestamp_and_oversized_entry(self):
        duplicate = (b'{"_SYSTEMD_UNIT":"' + a532.UNIT.encode() + b'",' +
                     b'"_SYSTEMD_UNIT":"' + a532.UNIT.encode() + b'",' +
                     b'"MESSAGE":"RC6_SOURCE_CYCLE_DISPATCH={}"}\n')
        for raw in (duplicate, b'X' * (a532.MAX_JOURNAL_BYTES + 1),
                    b'not-json\n', b'{}\n' * 81):
            with self.subTest(raw_size=len(raw)):
                self.assertEqual(a532.summarize_journal(raw, now=NOW), a532.default_receipt())

    def test_sanitizer_rejects_host_injection_forged_paper_and_wrong_types(self):
        report = a532.summarize_journal(marker(), now=NOW)
        for changes in ({"account_token": "secret"}, {"paper_ready": True},
                        {"ppi_book_verified": True}, {"provider_calls": 1},
                        {"marker": "VALIDATED_RUNTIME"},
                        {"reported_reason": "/root/.config/secrets"},
                        {"stage_public": "CREDENTIALS_OK"},
                        {"reported_status": "NO_VERIFICADO"},
                        {"marker_age": "UNKNOWN"}):
            with self.subTest(changes=changes):
                self.assertEqual(a532.sanitize_receipt(json.dumps(dict(report, **changes)).encode()),
                                 a532.default_receipt())
        self.assertEqual(a532.sanitize_receipt(b"not-json"), a532.default_receipt())
        self.assertEqual(a532.default_receipt()["journal_message_access_scope"],
                         "HOST_ONLY_IF_AVAILABLE")
        self.assertEqual(a532.default_receipt()["marker"], "NO_VERIFICADO")

    def test_bounded_reader_never_needs_journalctl_in_offhost_tests(self):
        good = a532.read_journal_bounded(["python3", "-c", "print('only-local-smoke')"])
        self.assertEqual(good, b"only-local-smoke\n")
        too_big = a532.read_journal_bounded(
            ["python3", "-c", f"print('x'*{a532.MAX_JOURNAL_BYTES + 1})"])
        self.assertIsNone(too_big)

    def test_workflow_contract_no_new_schedule_host_only_after_main_merge(self):
        wf = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("scripts/rc6_a532_source_stage_journal_ro.py", wf)
        self.assertIn("test_rc6_a532_source_stage_journal_ro.py", wf)
        self.assertIn("github.event_name != 'pull_request'", wf)
        self.assertNotIn("  schedule:", wf)
        self.assertIn("trap 'rm -f -- evidence/.a532-untrusted.json' EXIT", wf)
        self.assertIn("sudo -n timeout 10 python3 -B - host-probe", wf)
        self.assertIn("< scripts/rc6_a532_source_stage_journal_ro.py", wf)
        self.assertIn("python3 scripts/rc6_a532_source_stage_journal_ro.py sanitize", wf)


if __name__ == "__main__":
    unittest.main()
