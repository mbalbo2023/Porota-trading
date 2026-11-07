"""A670 adversarial OFFHOST tests: evergreen audited BYMA sessions, zero PPI calls.

No SDK, production host, credentials, Watch, real orders or real filesystem
paths are used. In CI the existing A630 engine has its own separate regression
module and the A669 guard pins the verified source Git blob before any SSH.
"""

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from scripts import rc6_a669_ppi_day_slot_guard as guard


SHA = "a" * 40
YAML = Path(__file__).resolve().parents[1] / ".github/workflows/rc6-a669-ppi-current-book-20261105.yml"
LEGACY = Path(__file__).resolve().parents[1] / ".github/workflows/rc6-a630-ppi-current-book-20261104.yml"


def utc(hour, minute, day=5, month=11, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class A669GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="a669-no-provider-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = self.root / "audited-engine.py"
        self.engine.write_bytes(b"# A669 test data, never executable provider code\n")
        self.blob = guard.git_blob_sha(self.engine.read_bytes())

    def gate(self, *, now=None, event="schedule", schedule="45 10 * * 1-5",
             manual="", repo=guard.REPOSITORY, ref=guard.CONTROL_REF,
             head=SHA, source=SHA, engine=None, expected_blob=None):
        return guard.eligible_slot(
            now_utc=now or utc(13, 45), event=event, schedule=schedule,
            manual_slot=manual, repository=repo, ref=ref,
            checkout_sha=head, source_sha=source,
            engine_path=engine or self.engine,
            expected_engine_blob=expected_blob or self.blob)

    def test_four_explicit_ART_timezone_crons_resolve_correct_slot(self):
        cases = (
            (utc(13, 45), "45 10 * * 1-5", "1045"),
            (utc(14, 20), "20 11 * * 1-5", "1120"),
            (utc(17, 30), "30 14 * * 1-5", "1430"),
            (utc(19, 30), "30 16 * * 1-5", "1630"),
        )
        self.assertEqual(len(guard.SCHEDULE_TO_SLOT), 4)
        for when, cron, target in cases:
            with self.subTest(cron=cron):
                self.assertEqual(self.gate(now=when, schedule=cron),
                                 (target, "PASS_WEEKDAY_SLOT_AND_ENGINE_ONLY"))

    def test_future_weekdays_are_not_code_expired_weekends_and_naive_hold(self):
        for valid in (utc(13, 45, 12), utc(13, 45, 20),
                      utc(13, 45, 3, month=12), utc(13, 45, 5, month=1, year=2027)):
            self.assertEqual(self.gate(now=valid)[0], "1045")
        for wrong in (utc(13, 45, 7), utc(13, 45, 8),
                      datetime(2026, 11, 5, 13, 45)):
            with self.subTest(now=wrong):
                slot, reason = self.gate(now=wrong)
                self.assertEqual(slot, "")
                self.assertIn(reason, ("ART_BUSINESS_WEEKDAY_REQUIRED",
                                       "AWARE_UTC_CLOCK_REQUIRED"))

    def test_schedule_lag_is_30_minutes_and_early_or_later_denied(self):
        self.assertEqual(self.gate(now=utc(14, 15))[0], "1045")
        self.assertEqual(self.gate(now=utc(14, 16))[0], "")
        self.assertEqual(self.gate(now=utc(13, 44))[0], "")
        self.assertEqual(self.gate(now=utc(20, 0), schedule="30 16 * * 1-5")[0], "1630")
        self.assertEqual(self.gate(now=utc(20, 1), schedule="30 16 * * 1-5")[0], "")

    def test_manual_bounded_60_minutes_not_an_arbitrary_time_or_slot(self):
        args = dict(event="workflow_dispatch", schedule="", manual="1045")
        self.assertEqual(self.gate(now=utc(14, 45), **args)[0], "1045")
        self.assertEqual(self.gate(now=utc(14, 46), **args)[0], "")
        self.assertEqual(self.gate(now=utc(13, 44), **args)[0], "")
        for invalid in ("", "0945", "1046", "1045\nRC6_A669_SLOT=1630", "0000"):
            self.assertEqual(self.gate(now=utc(13, 45), event="workflow_dispatch",
                                       schedule="", manual=invalid)[0], "")
        self.assertEqual(self.gate(event="pull_request")[0], "")
        self.assertEqual(self.gate(schedule="45 13 * * 3")[0], "")

    def test_official_market_calendar_limited_negotiation_no_settlement_and_stale(self):
        reasons = {date(2026,11,6):"Día del Bancario; jornada especial sin liquidación",
                   date(2026,11,9):"Visita de Su Santidad el Papa León XIV",
                   date(2026,11,10):"Visita de Su Santidad el Papa León XIV",
                   date(2026,11,23):"Día de la Soberanía Nacional"}
        calendar = SimpleNamespace(
            FUENTE_OFICIAL="https://www.byma.com.ar/mercado/calendario-bursatil",
            CALENDARIO_ACTUALIZADO_EL=date(2026,10,14),
            ANIOS_AUDITADOS=frozenset({2026}),
            es_dia_habil_operativo=lambda day: day.weekday()<5 and day not in reasons,
            motivo_no_operativo=lambda day: reasons.get(day))
        session = SimpleNamespace(TZ=ZoneInfo("America/Argentina/Buenos_Aires"),
            BYMA_PAPER_SPOT_OPEN=time(10,30),BYMA_PAPER_SPOT_CLOSE=time(17))
        for day in (5,12):
            self.assertEqual(guard.audited_market_session(utc(13,45,day), calendar,session),
                             (True,"BYMA_AUDITED_OPEN"))
        self.assertEqual(guard.audited_market_session(utc(13,45,6),calendar,session),
                         (True,"BYMA_LIMITED_TRADING_NO_SETTLEMENT_OBSERVE_ONLY"))
        for day in (9,10,23):
            self.assertEqual(guard.audited_market_session(utc(13,45,day), calendar,session)[0],False)
        self.assertEqual(guard.audited_market_session(utc(13,45,14,month=12), calendar,session)[1],
                         "MARKET_CALENDAR_NOT_VERIFIED")
        self.assertEqual(guard.audited_market_session(utc(13,45,5,month=1,year=2027), calendar,session)[1],
                         "MARKET_CALENDAR_NOT_VERIFIED")
        bad = SimpleNamespace(**{**vars(calendar),"FUENTE_OFICIAL":"https://example.com"})
        self.assertEqual(guard.audited_market_session(utc(13,45),bad,session)[0],False)

    def test_verified_market_calendar_blob_and_checkout_are_required(self):
        product = self.root / "product"
        product.mkdir()
        (product / "ak_byma_calendar.py").write_text("SAFE_CALENDAR=1\n")
        (product / "co_market_sessions_hf6.py").write_text("SAFE_SESSION=1\n")
        cal_blob=guard.git_blob_sha((product/"ak_byma_calendar.py").read_bytes())
        sess_blob=guard.git_blob_sha((product/"co_market_sessions_hf6.py").read_bytes())
        self.assertTrue(guard.pinned_product_calendar_files(product,cal_blob,sess_blob))
        (product/"ak_byma_calendar.py").write_text("SAFE_CALENDAR=2\n")
        self.assertFalse(guard.pinned_product_calendar_files(product,cal_blob,sess_blob))

    def test_only_actual_main_repo_and_exact_40hex_checkout_are_admitted(self):
        cases = (
            {"repo": "attacker/fork"},
            {"ref": "refs/heads/product"},
            {"ref": "refs/pull/2/merge"},
            {"head": "f" * 39},
            {"source": "b" * 40},
            {"head": "A" * 40, "source": "A" * 40},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.gate(**changes)[0], "")

    def test_engine_blob_change_missing_symlink_and_oversize_fail_closed(self):
        self.assertTrue(guard.pinned_engine_ok(self.engine, self.blob))
        self.engine.write_bytes(self.engine.read_bytes() + b"# tampered\n")
        self.assertEqual(self.gate()[1], "AUDITED_ENGINE_BLOB_MISMATCH")
        self.engine.unlink()
        self.assertEqual(self.gate()[1], "AUDITED_ENGINE_BLOB_MISMATCH")
        other = self.root / "target.py"
        other.write_bytes(b"same valid data")
        self.engine.symlink_to(other)
        self.assertEqual(self.gate()[1], "AUDITED_ENGINE_BLOB_MISMATCH")
        self.engine.unlink()
        self.engine.write_bytes(b"z" * 1_000_001)
        self.assertEqual(self.gate()[1], "AUDITED_ENGINE_BLOB_MISMATCH")

    def test_environment_attestation_written_only_after_proven_gate(self):
        destination = self.root / "github.env"
        destination.write_text("")
        self.assertTrue(guard._append_verified_env(destination, "1045",utc(13,45)))
        self.assertEqual(destination.read_text(),
                         "RC6_A669_SLOT=1045\n"
                         f"RC6_A669_ENGINE_BLOB={guard.ENGINE_GIT_BLOB}\n"
                         "RC6_A670_ART_DATE=2026-11-05\n"
                         "RC6_A670_MARKET_SESSION=BYMA_AUDITED_OPEN\n")
        self.assertFalse(guard._append_verified_env(destination,"1045",utc(13,45),
                         market_session="ARBITRARY_OPEN"))
        destination.unlink()
        destination.symlink_to(self.root / "missing.env")
        self.assertFalse(guard._append_verified_env(destination, "1045",utc(13,45)))

    def test_holiday_or_stale_calendar_hold_label_is_bounded_and_retained(self):
        destination = self.root / "github.env"
        destination.write_text("")
        self.assertTrue(guard._append_hold_env(destination,
            "AUDITED_BYMA_CLOSED_OR_HOLIDAY",utc(13,45,6)))
        self.assertEqual(destination.read_text(),
            "RC6_A670_HOLD_REASON=AUDITED_BYMA_CLOSED_OR_HOLIDAY\n"
            "RC6_A670_ART_DATE=2026-11-06\n")
        self.assertFalse(guard._append_hold_env(destination,"\nDO_HOST=evil",utc(13,45)))
        destination.unlink()
        destination.symlink_to(self.root / "missing.env")
        self.assertFalse(guard._append_hold_env(destination,
            "MARKET_CALENDAR_NOT_VERIFIED",utc(13,45)))

    def test_single_evergreen_weekday_scheduler_and_no_unbounded_provider_job(self):
        code = YAML.read_text()
        self.assertEqual(set(re.findall(r"cron: '([^']+)'", code)),
                         set(guard.SCHEDULE_TO_SLOT))
        self.assertEqual(code.count("timezone: 'America/Argentina/Buenos_Aires'"),4)
        self.assertNotIn("  schedule:", LEGACY.read_text())
        self.assertIn("  workflow_dispatch:", LEGACY.read_text())
        self.assertIn("github.ref == 'refs/heads/main'", code)
        self.assertIn("github.event_name == 'workflow_dispatch'", code)
        self.assertIn("python3 -B scripts/rc6_a669_ppi_day_slot_guard.py", code)
        self.assertIn("ref: e2e56e85aa98b4f7e45268e2a8a21f61f6d1c05b", code)
        self.assertIn("sparse-checkout:", code)
        self.assertIn("group: rc6-unified-paper-deploy", code)
        self.assertIn("cancel-in-progress: false", code)
        self.assertIn("RC6_A630_TARGET_B64", code)  # audited engine wire schema
        self.assertIn("verify_receipt(payload,target)", code)
        self.assertIn("provider_signature_verified':False", code)
        self.assertIn("source_pit_verified':False", code)
        self.assertIn("paper_admission_certified':False", code)
        self.assertIn("RC6_A670_HOLD_REASON", code)
        self.assertIn("RC6_A670_MARKET_SESSION", code)
        self.assertIn("BYMA_LIMITED_TRADING_NO_SETTLEMENT_OBSERVE_ONLY", code)
        self.assertNotIn("pull_request_target:", code)
        self.assertNotIn("porota-predeploy-v2", code)
        self.assertNotIn("porota-deploy-v2", code)
        self.assertNotIn("actions/upload-artifact@v3", code)

    def test_git_blob_is_not_unprefixed_sha1_and_expected_pin_is_literal(self):
        content = self.engine.read_bytes()
        self.assertNotEqual(guard.git_blob_sha(content),
                            __import__("hashlib").sha1(content).hexdigest())
        self.assertRegex(guard.ENGINE_GIT_BLOB, r"^[0-9a-f]{40}$")
        self.assertRegex(guard.CALENDAR_GIT_BLOB, r"^[0-9a-f]{40}$")
        self.assertRegex(guard.SESSIONS_GIT_BLOB, r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
