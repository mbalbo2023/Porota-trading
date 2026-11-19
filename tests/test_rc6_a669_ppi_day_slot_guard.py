"""A670 adversarial OFFHOST tests: evergreen audited BYMA sessions, zero PPI calls.

No SDK, production host, credentials, Watch, real orders or real filesystem
paths are used. In CI the existing A630 engine has its own separate regression
module and the A669 guard pins the verified source Git blob before any SSH.
"""

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from contextlib import redirect_stdout
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
import yaml

from scripts import rc6_a669_ppi_day_slot_guard as guard


SHA = "a" * 40
YAML = Path(__file__).resolve().parents[1] / ".github/workflows/rc6-a669-ppi-current-book-20261105.yml"


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

    def gate(self, *, now=None, event="workflow_dispatch", schedule="",
             manual="1045", repo=guard.REPOSITORY, ref=guard.CONTROL_REF,
             head=SHA, source=SHA, engine=None, expected_blob=None):
        return guard.eligible_slot(
            now_utc=now or utc(13, 45), event=event, schedule=schedule,
            manual_slot=manual, repository=repo, ref=ref,
            checkout_sha=head, source_sha=source,
            engine_path=engine or self.engine,
            expected_engine_blob=expected_blob or self.blob)

    def command_gate(self, **changes):
        arguments = dict(
            now_utc=utc(20, 15, 18), event="workflow_dispatch",
            repository=guard.REPOSITORY, ref=guard.CONTROL_REF,
            checkout_sha=SHA, source_sha=SHA, art_date="2026-11-18",
            family="BONOS", pilot_mode="OFF", postclose_mode="OFF",
            engine_path=self.engine, expected_engine_blob=self.blob)
        arguments.update(changes)
        return guard.eligible_observer_command(**arguments)

    def open_command_gate(self, **changes):
        arguments = dict(
            now_utc=utc(13, 45, 19), event="workflow_dispatch",
            repository=guard.REPOSITORY, ref=guard.CONTROL_REF,
            checkout_sha=SHA, source_sha=SHA, art_date="2026-11-19",
            family="ALL", pilot_mode="OFF", postclose_mode="OFF",
            engine_path=self.engine, expected_engine_blob=self.blob)
        arguments.update(changes)
        return guard.eligible_open_observer_command(**arguments)

    def test_open_batch_emits_only_one_typed_command_during_verified_art_window(self):
        self.assertEqual(self.open_command_gate(),
                         ("PPI_DIRECT_REST_OPEN_ONCE:2026-11-19:ALL",
                          "PASS_MANUAL_OPEN_TEN_FAMILY_OBSERVER_COMMAND_ONLY"))
        cases = (
            ({"event": "schedule"}, "MANUAL_DISPATCH_REQUIRED"),
            ({"family": "BONOS"}, "OBSERVER_OPEN_ALL_ONLY"),
            ({"family": "ALL;touch /tmp/foo"}, "OBSERVER_OPEN_ALL_ONLY"),
            ({"now_utc": utc(13, 29, 19)}, "OUTSIDE_AUDITED_BYMA_SPOT_WINDOW"),
            ({"now_utc": utc(20, 0, 19)}, "OUTSIDE_AUDITED_BYMA_SPOT_WINDOW"),
            ({"now_utc": utc(13, 45, 21)}, "OBSERVER_COMMAND_ART_DATE_MISMATCH"),
            ({"pilot_mode": "ONCE"}, "OBSERVER_COMMAND_SAMPLE_MODE_FORBIDDEN"),
            ({"postclose_mode": "ONCE"}, "OBSERVER_COMMAND_SAMPLE_MODE_FORBIDDEN"),
            ({"expected_engine_blob": "0"*40}, "AUDITED_ENGINE_BLOB_MISMATCH"),
        )
        for args, reason in cases:
            with self.subTest(args=args):
                self.assertEqual(self.open_command_gate(**args), ("", reason))

    def test_open_main_gate_rejects_unverified_promote_even_with_env_singleton_claim(self):
        with tempfile.TemporaryDirectory(prefix="a670-open-one-") as temp:
            env_path = Path(temp) / "gh-env"
            env_path.touch()
            environment = {"RC6_A670_ISSUER_MODE": "observer_open_ten_family_command",
                           "RC6_A670_OBSERVER_ART_DATE": "2026-11-19",
                           "RC6_A670_OBSERVER_FAMILY": "ALL",
                           "RC6_A630_PILOT_MODE": "OFF", "RC6_A630_POSTCLOSE_MODE": "OFF",
                           "GITHUB_EVENT_NAME": "workflow_dispatch",
                           "GITHUB_REPOSITORY": guard.REPOSITORY,
                           "GITHUB_REF": guard.CONTROL_REF, "GITHUB_SHA": SHA,
                           "GITHUB_ENV": str(env_path),
                           "RC6_PPI_SINGLE_SESSION_GLOBAL_ATTESTED": "1"}
            with patch.dict(os.environ, environment), \
                 patch.object(guard.subprocess, "check_output", return_value=SHA+"\n"), \
                 patch.object(guard, "pinned_engine_ok", return_value=True), \
                 patch.object(guard, "_trusted_promote_claim",
                              side_effect=ValueError("PROMOTE_ARTIFACT_CUSTODY_HOLD")), \
                 patch.object(guard, "_official_session_check",
                              return_value=(True, guard.SESSION_REGULAR)), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(guard.main(now_utc=utc(13, 45, 19)), 2)
            output = env_path.read_text()
        self.assertIn("RC6_A670_HOLD_REASON=OBSERVER_COMMAND_RELEASE_UNPINNED\n", output)
        self.assertNotIn("RC6_A670_OBSERVER_COMMAND=", output)
        self.assertNotIn("PPI_DIRECT_REST_POSTCLOSE_ONCE", output)

    def test_postclose_main_gate_rejects_unverified_promote_before_host(self):
        # The existing scanner may be logged in, but exit-reader/scalping
        # are other login_once actors. No POSTCLOSE GET without global proof.
        with tempfile.TemporaryDirectory(prefix="a670-postclose-global-hold-") as temp:
            env_path = Path(temp) / "gh-env"
            env_path.touch()
            environment = {"RC6_A670_ISSUER_MODE": "observer_postclose_command",
                           "RC6_A670_OBSERVER_ART_DATE": "2026-11-19",
                           "RC6_A670_OBSERVER_FAMILY": "BONOS",
                           "RC6_A630_PILOT_MODE": "OFF", "RC6_A630_POSTCLOSE_MODE": "OFF",
                           "GITHUB_EVENT_NAME": "workflow_dispatch",
                           "GITHUB_REPOSITORY": guard.REPOSITORY,
                           "GITHUB_REF": guard.CONTROL_REF, "GITHUB_SHA": SHA,
                           "GITHUB_ENV": str(env_path),
                           "RC6_PPI_SINGLE_SESSION_GLOBAL_ATTESTED": "1"}
            with patch.dict(os.environ, environment), \
                 patch.object(guard.subprocess, "check_output", return_value=SHA+"\n"), \
                 patch.object(guard, "pinned_engine_ok", return_value=True), \
                 patch.object(guard, "_trusted_promote_claim",
                              side_effect=ValueError("PROMOTE_ARTIFACT_CUSTODY_HOLD")), \
                 patch.object(guard, "_official_postclose_check",
                              return_value=(True, guard.SESSION_POSTCLOSE)), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(guard.main(now_utc=utc(20, 15, 19)), 2)
            output = env_path.read_text()
        self.assertIn("RC6_A670_HOLD_REASON=OBSERVER_COMMAND_RELEASE_UNPINNED\n", output)
        self.assertNotIn("RC6_A670_OBSERVER_COMMAND=", output)

    def test_open_main_gate_holds_before_host_for_unaudited_day(self):
        with tempfile.TemporaryDirectory(prefix="a670-open-hold-") as temp:
            env_path = Path(temp) / "gh-env"
            env_path.touch()
            environment = {"RC6_A670_ISSUER_MODE": "observer_open_ten_family_command",
                           "RC6_A670_OBSERVER_ART_DATE": "2026-11-19",
                           "RC6_A670_OBSERVER_FAMILY": "ALL",
                           "RC6_A630_PILOT_MODE": "OFF", "RC6_A630_POSTCLOSE_MODE": "OFF",
                           "GITHUB_EVENT_NAME": "workflow_dispatch",
                           "GITHUB_REPOSITORY": guard.REPOSITORY,
                           "GITHUB_REF": guard.CONTROL_REF, "GITHUB_SHA": SHA,
                           "GITHUB_ENV": str(env_path)}
            with patch.dict(os.environ, environment), \
                 patch.object(guard.subprocess, "check_output", return_value=SHA+"\n"), \
                 patch.object(guard, "pinned_engine_ok", return_value=True), \
                 patch.object(guard, "_official_session_check",
                              return_value=(False, "MARKET_CALENDAR_NOT_VERIFIED")), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(guard.main(now_utc=utc(13, 45, 19)), 2)
            output = env_path.read_text()
        self.assertIn("RC6_A670_HOLD_REASON=MARKET_CALENDAR_NOT_VERIFIED\n", output)
        self.assertNotIn("RC6_A670_OBSERVER_COMMAND=", output)

    def test_open_main_gate_rejects_current_p0_release_without_open_handler(self):
        from scripts import rc6_a630_host_ppi_current_book as engine
        with tempfile.TemporaryDirectory(prefix="a670-open-p0-hold-") as temp:
            env_path = Path(temp) / "gh-env"
            env_path.touch()
            environment = {"RC6_A670_ISSUER_MODE": "observer_open_ten_family_command",
                           "RC6_A670_OBSERVER_ART_DATE": "2026-11-19",
                           "RC6_A670_OBSERVER_FAMILY": "ALL",
                           "RC6_A630_PILOT_MODE": "OFF", "RC6_A630_POSTCLOSE_MODE": "OFF",
                           "GITHUB_EVENT_NAME": "workflow_dispatch",
                           "GITHUB_REPOSITORY": guard.REPOSITORY,
                           "GITHUB_REF": guard.CONTROL_REF, "GITHUB_SHA": SHA,
                           "GITHUB_ENV": str(env_path),
                           "RC6_P0_EXPECTED_CANDIDATE_SHA": engine.ROOTLESS_CANDIDATE_SHA,
                           "RC6_P0_EXPECTED_TREE_SHA": engine.ROOTLESS_CANDIDATE_TREE,
                           "RC6_P0_EXPECTED_IMAGE_ID": engine.ROOTLESS_IMAGE_ID,
                           "RC6_P0_EXPECTED_RELEASE_MANIFEST_SHA256": "a"*64}
            with patch.dict(os.environ, environment), \
                 patch.object(guard.subprocess, "check_output", return_value=SHA+"\n"), \
                 patch.object(guard, "pinned_engine_ok", return_value=True), \
                 patch.object(guard, "_trusted_promote_claim",
                              return_value={"source_sha": engine.ROOTLESS_CANDIDATE_SHA,
                               "source_tree": engine.ROOTLESS_CANDIDATE_TREE,
                               "image_id": engine.ROOTLESS_IMAGE_ID,
                               "release_manifest_sha256": "a" * 64,
                               "observer_cid": "1" * 64, "dashboard_cid": "2" * 64,
                               "promote_run_id": 44442769638,
                               "promote_run_attempt": 1,
                               "runtime_artifact_digest": "sha256:" + "1" * 64}), \
                 patch.object(guard, "_official_session_check",
                              return_value=(True, guard.SESSION_REGULAR)), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(guard.main(now_utc=utc(13, 45, 19)), 2)
            output = env_path.read_text()
        self.assertIn("RC6_A670_HOLD_REASON=OBSERVER_OPEN_PRODUCT_DEPLOY_UNVERIFIED\n", output)
        self.assertNotIn("RC6_A670_OBSERVER_COMMAND=", output)

    def test_new_promote_claim_only_allows_runner_to_attempt_host_physical_custody(self):
        claim = {"source_sha": "d" * 40, "source_tree": "e" * 40,
                 "image_id": "sha256:" + "f" * 64,
                 "release_manifest_sha256": "c" * 64,
                 "observer_cid": "1" * 64, "dashboard_cid": "2" * 64,
                 "promote_run_id": 888, "promote_run_attempt": 2,
                 "runtime_artifact_digest": "sha256:" + "3" * 64}
        with tempfile.TemporaryDirectory(prefix="a670-new-promote-") as temp:
            env_path = Path(temp) / "gh-env"
            env_path.touch()
            values = {"RC6_A670_ISSUER_MODE": "observer_open_ten_family_command",
                      "RC6_A670_OBSERVER_ART_DATE": "2026-11-19",
                      "RC6_A670_OBSERVER_FAMILY": "ALL", "RC6_A630_PILOT_MODE": "OFF",
                      "RC6_A630_POSTCLOSE_MODE": "OFF",
                      "GITHUB_EVENT_NAME": "workflow_dispatch",
                      "GITHUB_REPOSITORY": guard.REPOSITORY,
                      "GITHUB_REF": guard.CONTROL_REF, "GITHUB_SHA": SHA,
                      "GITHUB_ENV": str(env_path),
                      "RC6_PPI_SINGLE_SESSION_GLOBAL_ATTESTED": "1",
                      "RC6_P0_EXPECTED_CANDIDATE_SHA": "0" * 40}
            with patch.dict(os.environ, values), \
                 patch.object(guard.subprocess, "check_output", return_value=SHA+"\n"), \
                 patch.object(guard, "pinned_engine_ok", return_value=True), \
                 patch.object(guard, "_trusted_promote_claim", return_value=claim), \
                 patch.object(guard, "_official_session_check",
                              return_value=(True, guard.SESSION_REGULAR)), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(guard.main(now_utc=utc(13, 45, 19)), 0)
            rows = env_path.read_text().splitlines()
        self.assertIn("RC6_A670_OBSERVER_COMMAND=PPI_DIRECT_REST_OPEN_ONCE:2026-11-19:ALL", rows)
        self.assertIn("RC6_P0_EXPECTED_CANDIDATE_SHA=" + "d" * 40, rows)
        self.assertIn("RC6_P0_EXPECTED_TREE_SHA=" + "e" * 40, rows)
        self.assertIn("RC6_P0_EXPECTED_IMAGE_ID=sha256:" + "f" * 64, rows)
        self.assertIn("RC6_P0_EXPECTED_OBSERVER_CID=" + "1" * 64, rows)
        self.assertIn("RC6_P0_EXPECTED_DASHBOARD_CID=" + "2" * 64, rows)
        self.assertIn("RC6_A670_PROMOTE_ARTIFACT_DIGEST=sha256:" + "3" * 64, rows)
        self.assertNotIn("RC6_PPI_SINGLE_SESSION_GLOBAL_ATTESTED=1", rows)

    def test_observer_command_manual_gate_never_uses_sample_slot(self):
        self.assertEqual(self.command_gate(),
                         ("PPI_DIRECT_REST_POSTCLOSE_ONCE:2026-11-18:BONOS",
                          "PASS_MANUAL_POSTCLOSE_OBSERVER_COMMAND_ONLY"))
        for family in ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
                       "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"):
            with self.subTest(family=family):
                self.assertTrue(self.command_gate(family=family)[0].endswith(":" + family))

    def test_observer_command_rejects_nonmanual_invalid_day_or_old_pilot(self):
        invalid = (
            ({"event": "schedule"}, "MANUAL_DISPATCH_REQUIRED"),
            ({"ref": "refs/heads/deploy/rc6-pr69-isolated-20260915"},
             "NONCANONICAL_REPOSITORY_OR_REF"),
            ({"art_date": "2026-11-17"}, "OBSERVER_COMMAND_ART_DATE_MISMATCH"),
            ({"art_date": "2026-11-18;touch /tmp/injected"},
             "OBSERVER_COMMAND_ART_DATE_INVALID"),
            ({"family": "BONOS;touch /tmp/injected"}, "OBSERVER_COMMAND_FAMILY_INVALID"),
            ({"pilot_mode": "ONCE"}, "OBSERVER_COMMAND_SAMPLE_MODE_FORBIDDEN"),
            ({"postclose_mode": "ONCE"}, "OBSERVER_COMMAND_SAMPLE_MODE_FORBIDDEN"),
            ({"now_utc": utc(19, 59, 18)}, "OUTSIDE_POSTCLOSE_DIAGNOSTIC_WINDOW"),
            ({"now_utc": utc(2, 1, 19)}, "OUTSIDE_POSTCLOSE_DIAGNOSTIC_WINDOW"),
            ({"now_utc": utc(20, 15, 21)}, "OBSERVER_COMMAND_ART_DATE_MISMATCH"),
            ({"source_sha": "b" * 40}, "EXACT_CHECKOUT_SHA_REQUIRED"),
            ({"expected_engine_blob": "0" * 40}, "AUDITED_ENGINE_BLOB_MISMATCH"),
        )
        for overrides, reason in invalid:
            with self.subTest(reason=reason, overrides=overrides):
                self.assertEqual(self.command_gate(**overrides), ("", reason))

    def test_observer_dispatch_job_cannot_trigger_legacy_provider_job(self):
        workflow = yaml.load(YAML.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["on"]["workflow_dispatch"]["inputs"]
                         ["issuer_mode"]["default"], "observer_postclose_command")
        self.assertEqual(workflow["on"]["workflow_dispatch"]["inputs"]
                         ["issuer_mode"]["options"],
                         ["observer_postclose_command", "observer_open_ten_family_command"])
        sample = workflow["jobs"]["sample_main_only"]
        issuer = workflow["jobs"]["queue_observer_main_only"]
        self.assertEqual(sample["if"], "false")
        self.assertIn("inputs.issuer_mode == 'observer_postclose_command'", issuer["if"])
        self.assertIn("github.actor == 'mbalbo2023'", issuer["if"])
        self.assertIn("github.triggering_actor == 'mbalbo2023'", issuer["if"])
        self.assertEqual(issuer["concurrency"]["group"], "rc6-unified-paper-deploy")
        self.assertEqual(issuer["concurrency"]["cancel-in-progress"], "false")
        self.assertNotIn("schedule", workflow["on"])

    def test_open_workflow_reuses_scanner_queue_and_global_safety_mutex(self):
        workflow = yaml.load(YAML.read_text(), Loader=yaml.BaseLoader)
        issuer = workflow["jobs"]["queue_observer_main_only"]
        choices = workflow["on"]["workflow_dispatch"]["inputs"]["issuer_mode"]["options"]
        self.assertEqual(choices, ["observer_postclose_command",
                                   "observer_open_ten_family_command"])
        self.assertEqual(workflow["jobs"]["sample_main_only"]["if"], "false")
        self.assertIn("inputs.issuer_mode == 'observer_open_ten_family_command'", issuer["if"])
        self.assertEqual(issuer["concurrency"]["group"], "rc6-unified-paper-deploy")
        self.assertEqual(issuer["concurrency"]["cancel-in-progress"], "false")
        runs = "\n".join(str(step.get("run", "")) for step in issuer["steps"])
        self.assertIn("rootless-enqueue-open", runs)
        self.assertIn("PPI_DIRECT_REST_OPEN_ONCE", runs)
        self.assertIn("verified_command_wrapper", runs)
        self.assertNotIn("login_once", runs)
        self.assertNotIn("container-capture", runs)

    def test_observer_workflow_dispatch_refuses_legacy_sample_even_if_forced(self):
        with patch.dict(os.environ, {"RC6_A670_ISSUER_MODE": "sample",
                                  "GITHUB_EVENT_NAME": "workflow_dispatch"}), \
             patch.object(guard, "eligible_slot", return_value=("1045", "PASS")) as legacy, \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(guard.main(now_utc=utc(13, 45)), 2)
        legacy.assert_not_called()
        self.assertIn("LEGACY_SECOND_PPI_SESSION_FORBIDDEN", output.getvalue())

    def test_observer_failed_guard_artifact_does_not_publish_raw_dispatch_input(self):
        workflow = yaml.load(YAML.read_text(), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["queue_observer_main_only"]["steps"]
        fallback = next(step["run"] for step in steps
                        if step.get("name", "").startswith("Always retain sanitized"))
        with tempfile.TemporaryDirectory(prefix="p0-no-private-input-") as directory:
            env = dict(os.environ, GITHUB_RUN_ID="7", GITHUB_SHA=SHA,
                RC6_A670_OBSERVER_ART_DATE="SECRET_ACCOUNT_TOKEN_DO_NOT_UPLOAD",
                RC6_A670_OBSERVER_FAMILY="SECRET_API_KEY_DO_NOT_UPLOAD")
            proc = subprocess.run(["bash", "-c", fallback], cwd=directory,
                                  env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads((Path(directory)/"evidence/p0_observer_command.json").read_text())
        self.assertEqual(payload["art_date"], "")
        self.assertEqual(payload["family"], "")

    def test_four_explicit_ART_manual_slots_resolve_without_cron(self):
        cases = (
            (utc(13, 45), "45 10 * * 1-5", "1045"),
            (utc(14, 20), "20 11 * * 1-5", "1120"),
            (utc(17, 30), "30 14 * * 1-5", "1430"),
            (utc(19, 30), "30 16 * * 1-5", "1630"),
        )
        for when, cron, target in cases:
            with self.subTest(slot=target):
                self.assertEqual(self.gate(now=when, manual=target),
                                 (target, "PASS_WEEKDAY_SLOT_AND_ENGINE_ONLY"))
                self.assertEqual(self.gate(now=when, event="schedule", schedule=cron,
                                           manual=target),
                                 ("", "MANUAL_DISPATCH_REQUIRED"))

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

    def test_manual_slot_60_minute_window_and_early_or_later_denied(self):
        self.assertEqual(self.gate(now=utc(14, 45))[0], "1045")
        self.assertEqual(self.gate(now=utc(14, 46))[0], "")
        self.assertEqual(self.gate(now=utc(13, 44))[0], "")
        self.assertEqual(self.gate(now=utc(20, 30), manual="1630")[0], "1630")
        self.assertEqual(self.gate(now=utc(20, 31), manual="1630")[0], "")

    def test_manual_bounded_60_minutes_not_an_arbitrary_time_or_slot(self):
        args = dict(event="workflow_dispatch", schedule="", manual="1045")
        self.assertEqual(self.gate(now=utc(14, 45), **args)[0], "1045")
        self.assertEqual(self.gate(now=utc(14, 46), **args)[0], "")
        self.assertEqual(self.gate(now=utc(13, 44), **args)[0], "")
        for invalid in ("", "0945", "1046", "1045\nRC6_A669_SLOT=1630", "0000"):
            self.assertEqual(self.gate(now=utc(13, 45), event="workflow_dispatch",
                                       schedule="", manual=invalid)[0], "")
        self.assertEqual(self.gate(event="pull_request")[0], "")
        self.assertEqual(self.gate(event="schedule",schedule="45 13 * * 3")[1],
                         "MANUAL_DISPATCH_REQUIRED")

    def test_postclose_manual_gate_is_separate_from_live_slots_and_pilot_only(self):
        params = dict(now_utc=utc(23, 0, day=18), event="workflow_dispatch",
            manual_slot="1630", repository=guard.REPOSITORY,
            ref=guard.CONTROL_REF, checkout_sha=SHA, source_sha=SHA,
            engine_path=self.engine, expected_engine_blob=self.blob,
            pilot_mode="ONCE")
        self.assertEqual(guard.eligible_postclose(**params),
                         ("1630", "PASS_POSTCLOSE_MANUAL_ENGINE_ONLY"))
        self.assertEqual(self.gate(now=params["now_utc"], event="workflow_dispatch",
                                   manual="1630")[0], "")
        for edit in ({"event": "schedule"}, {"event": "pull_request"},
                     {"pilot_mode": "OFF"}, {"manual_slot": "0000"},
                     {"now_utc": utc(19, 55, day=18)},
                     {"now_utc": utc(2, 0, day=19)},
                     {"repository": "attacker/fork"},
                     {"source_sha": "b" * 40}):
            with self.subTest(edit=edit):
                self.assertEqual(guard.eligible_postclose(**(params | edit))[0], "")

    def test_postclose_audit_is_bounded_and_does_not_claim_market_open(self):
        reasons = {
            date(2026,11,6): "Día del Bancario; jornada especial sin liquidación",
            date(2026,11,9): "Visita de Su Santidad el Papa León XIV",
            date(2026,11,10): "Visita de Su Santidad el Papa León XIV",
        }
        calendar = SimpleNamespace(
            FUENTE_OFICIAL="https://www.byma.com.ar/mercado/calendario-bursatil",
            CALENDARIO_ACTUALIZADO_EL=date(2026, 10, 14),
            ANIOS_AUDITADOS=frozenset({2026}),
            es_dia_habil_operativo=lambda day: day.weekday()<5 and day not in reasons,
            motivo_no_operativo=lambda day: reasons.get(day))
        session = SimpleNamespace(TZ=ZoneInfo("America/Argentina/Buenos_Aires"),
            BYMA_PAPER_SPOT_OPEN=time(10,30), BYMA_PAPER_SPOT_CLOSE=time(17))
        self.assertEqual(guard.audited_postclose_window(utc(23,0,day=18),calendar,session),
                         (True,"BYMA_CLOSED_POSTCLOSE_DIAGNOSTIC_ONLY"))
        self.assertEqual(guard.audited_postclose_window(utc(23,0,day=6),calendar,session),
                         (False,"BYMA_LIMITED_NO_SETTLEMENT_POSTCLOSE_HOLD"))
        for day in (9, 10):
            self.assertEqual(guard.audited_postclose_window(utc(23,0,day=day),calendar,session),
                             (False,"BYMA_NO_NEGOTIATION_OR_SPECIAL_SESSION_HOLD"))
        self.assertEqual(guard.audited_postclose_window(utc(19,55,day=18),calendar,session)[0],False)
        self.assertEqual(guard.audited_postclose_window(utc(2,0,day=19),calendar,session)[0],False)
        self.assertEqual(guard.audited_postclose_window(utc(23,0,day=21),calendar,session)[0],False)
        stale = SimpleNamespace(**{**vars(calendar),"CALENDARIO_ACTUALIZADO_EL":date(2026,8,1)})
        self.assertEqual(guard.audited_postclose_window(utc(23,0,day=18),stale,session)[0],False)
        for broken in (
            SimpleNamespace(**{k:v for k,v in vars(calendar).items()
                               if k!="es_dia_habil_operativo"}),
            SimpleNamespace(**{**vars(calendar),
                               "es_dia_habil_operativo":lambda day: None}),
            SimpleNamespace(**{**vars(calendar),
                               "motivo_no_operativo":lambda day: "UNKNOWN_CLOSURE"}),
        ):
            self.assertEqual(guard.audited_postclose_window(utc(23,0,day=18),broken,session)[0],False)

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

    def test_manual_only_workflow_has_no_scheduler_or_unbounded_provider_job(self):
        code = YAML.read_text()
        self.assertNotIn("\n  schedule:\n", code)
        self.assertNotRegex(code, r"\bcron\s*:")
        self.assertNotIn("github.event_name == 'schedule'", code)
        self.assertNotIn("RC6_A669_SCHEDULE: ${{ github.event.schedule }}", code)
        self.assertIn("\n  workflow_dispatch:\n", code)
        self.assertIn("github.ref == 'refs/heads/main'", code)
        self.assertIn("github.event_name == 'workflow_dispatch'", code)
        self.assertIn("python3 -B scripts/rc6_a669_ppi_day_slot_guard.py", code)
        self.assertIn("ref: 7e1e744d6ca9ab30c51b5747dd4467af5ece0145", code)
        self.assertIn("sparse-checkout:", code)
        self.assertIn("group: rc6-unified-paper-deploy", code)
        self.assertIn("cancel-in-progress: false", code)
        self.assertIn("RC6_A630_TARGET_B64", code)  # audited engine wire schema
        self.assertIn("verify_receipt(payload,target)", code)
        self.assertIn("provider_signature_verified':False", code)
        self.assertIn("source_pit_verified':False", code)
        self.assertIn("paper_admission_certified':False", code)
        self.assertIn("RC6_A670_HOLD_REASON", code)

    def test_local_pilot_requires_manual_workflow_dispatch_and_is_off_by_default(self):
        code = YAML.read_text()
        self.assertIn("pilot_mode:", code)
        self.assertIn("options: ['off', 'once']", code)
        self.assertIn("github.event_name == 'workflow_dispatch'", code)
        self.assertIn("RC6_A630_PILOT_MODE", code)
        self.assertIn("RC6_A630_LOCAL_PILOT", code)
        self.assertIn("RC6_A670_MARKET_SESSION", code)
        self.assertIn("BYMA_LIMITED_TRADING_NO_SETTLEMENT_OBSERVE_ONLY", code)
        self.assertNotIn("pull_request_target:", code)
        self.assertNotIn("porota-predeploy-v2", code)
        self.assertNotIn("porota-deploy-v2", code)
        self.assertNotIn("actions/upload-artifact@v3", code)

    def test_postclose_explicit_manual_only_without_background_schedule(self):
        code = YAML.read_text()
        self.assertIn("postclose_mode:", code)
        self.assertIn("RC6_A630_POSTCLOSE_MODE", code)
        self.assertIn("RC6_A630_POSTCLOSE", code)
        self.assertIn("PPI_NATIVE_STALE_POSTCLOSE_DIAGNOSTIC_NO_GO", code)
        self.assertIn("sample_families", code)

    def test_git_blob_is_not_unprefixed_sha1_and_expected_pin_is_literal(self):
        content = self.engine.read_bytes()
        self.assertNotEqual(guard.git_blob_sha(content),
                            __import__("hashlib").sha1(content).hexdigest())
        self.assertRegex(guard.ENGINE_GIT_BLOB, r"^[0-9a-f]{40}$")
        self.assertRegex(guard.CALENDAR_GIT_BLOB, r"^[0-9a-f]{40}$")
        self.assertRegex(guard.SESSIONS_GIT_BLOB, r"^[0-9a-f]{40}$")

    def test_current_engine_blob_is_pinned_before_ssh(self):
        source = YAML.parents[2] / "scripts/rc6_a630_host_ppi_current_book.py"
        self.assertTrue(guard.pinned_engine_ok(source),
                        "A670 MUST re-pin reviewed changed A630 source before SSH")

    def test_issuer_release_pins_come_only_from_promote_artifact_not_env(self):
        workflow = yaml.load(YAML.read_text(), Loader=yaml.BaseLoader)
        job = workflow["jobs"]["queue_observer_main_only"]
        for key in ("RC6_P0_EXPECTED_CANDIDATE_SHA", "RC6_P0_EXPECTED_TREE_SHA",
                    "RC6_P0_EXPECTED_IMAGE_ID",
                    "RC6_P0_EXPECTED_RELEASE_MANIFEST_SHA256"):
            self.assertNotIn(key, job["env"])
        self.assertEqual(workflow["permissions"]["actions"], "read")
        preflight = next(step for step in job["steps"] if "OFFHOST same-day" in step.get("name", ""))
        self.assertEqual(preflight["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertNotIn("PPI_SINGLE_SESSION_GLOBAL_ATTESTED", YAML.read_text())
        self.assertNotIn("_issuer_pin_ready", YAML.read_text())
        ssh = next(step for step in job["steps"]
                   if "Strict private rootless SSH" in step.get("name", ""))
        body = ssh["run"]
        for name in ("RC6_P0_EXPECTED_CANDIDATE_SHA", "RC6_P0_EXPECTED_TREE_SHA",
                     "RC6_P0_EXPECTED_IMAGE_ID",
                     "RC6_P0_EXPECTED_RELEASE_MANIFEST_SHA256",
                     "RC6_P0_EXPECTED_OBSERVER_CID", "RC6_P0_EXPECTED_DASHBOARD_CID",
                     "RC6_A670_PROMOTE_ARTIFACT_DIGEST"):
            self.assertIn("'$" + name + "'", body)
        self.assertIn("'global_ppi_single_session_verified':False", body)
        self.assertNotIn("GH_TOKEN", body)

    def test_gh_api_refuses_missing_token_and_never_calls_gh_without_it(self):
        with patch.dict(os.environ, {"GH_TOKEN": ""}), \
             patch.object(guard.subprocess, "Popen") as api:
            with self.assertRaisesRegex(ValueError, "PROMOTE_ARTIFACT_CUSTODY_HOLD"):
                guard._trusted_promote_claim()
            api.assert_not_called()

    def test_gh_cli_downloads_three_promote_endpoints_only_and_validates_binary_zip(self):
        from hashlib import sha256
        run = {"id": 888, "run_attempt": 2, "status": "completed",
               "conclusion": "success", "event": "workflow_dispatch",
               "path": ".github/workflows/porota-deploy-v2-promote.yml",
               "head_branch": "deploy/rc6-pr69-isolated-20260915",
               "head_sha": "a" * 40,
               "head_repository": {"full_name": guard.REPOSITORY}}
        runtime = {"schema": "porota.rootless.deploy.v1", "state": "VALIDADO_RUNTIME",
                   "phase": "COMPLETE", "source_sha": "d" * 40,
                   "source_tree": "e" * 40, "image_id": "sha256:" + "f" * 64,
                   "release_manifest_sha256": "c" * 64,
                   "operating_mode": "PRODUCTION_PAPER", "execution_mode": "SIMULATION",
                   "real_orders_sent": 0, "ppi_watch_touched": False,
                   "roles": {"observer": "1" * 64, "dashboard": "2" * 64},
                   "health": {"observer": "running", "dashboard": "healthy",
                              "orders_guard": "BLOCKED"}}
        download = {"schema": "porota.release.download.v1",
                    "deployment_sha": "a" * 40, "source_sha": "d" * 40,
                    "source_tree": "e" * 40, "image_id": "sha256:" + "f" * 64,
                    "run_id": 777, "run_attempt": 1, "artifact_id": 444,
                    "artifact_digest": "sha256:" + "3" * 64}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("runtime.json", json.dumps(runtime))
            archive.writestr("download.json", json.dumps(download))
        payload = stream.getvalue()
        artifact = {"id": 500, "name": "porota-runtime-888-2", "expired": False,
                    "size_in_bytes": len(payload),
                    "digest": "sha256:" + sha256(payload).hexdigest(),
                    "workflow_run": {"id": 888, "head_sha": "a" * 40}}
        endpoints = []
        def transport(path, *, max_bytes):
            endpoints.append(path)
            self.assertLessEqual(max_bytes, 262144)
            if path.endswith("/zip"):
                self.assertEqual(max_bytes, 65536)
            if "/workflows/porota-deploy-v2-promote.yml/runs?" in path:
                output = json.dumps({"workflow_runs": [run]}).encode()
            elif path.endswith("/actions/runs/888/artifacts?per_page=100"):
                output = json.dumps({"artifacts": [artifact]}).encode()
            elif path.endswith("/actions/artifacts/500/zip"):
                output = payload
            else:
                self.fail("unexpected GH endpoint: " + path)
            return output
        with patch.dict(os.environ, {"GH_TOKEN": "synthetic-action-token"}), \
             patch.object(guard, "_bounded_gh_api", side_effect=transport):
            claim = guard._trusted_promote_claim()
        self.assertEqual(claim["source_sha"], "d" * 40)
        self.assertEqual(claim["runtime_artifact_digest"], artifact["digest"])
        self.assertEqual(len(endpoints), 3)
        self.assertTrue(all(arg.startswith("repos/mbalbo2023/Porota-trading/actions/")
                            for arg in endpoints))

    def test_gh_cli_reader_never_buffers_oversized_server_stdout(self):
        original = subprocess.Popen
        spawned = []
        def fake_gh(argv, **kwargs):
            self.assertEqual(argv[:4], ["gh", "api", "-H",
                                         "Accept: application/vnd.github+json"])
            self.assertEqual(kwargs.get("stdout"), subprocess.PIPE)
            self.assertEqual(kwargs.get("stderr"), subprocess.DEVNULL)
            count = 32 if not spawned else 100000
            process = original([sys.executable, "-c",
                "import sys;sys.stdout.buffer.write(b'x'*" + str(count) + ")"], **kwargs)
            spawned.append(process)
            return process
        with patch.object(guard.subprocess, "Popen", side_effect=fake_gh):
            self.assertEqual(guard._bounded_gh_api(
                "repos/mbalbo2023/Porota-trading/actions/runs/1/artifacts", max_bytes=100),
                b"x" * 32)
            with self.assertRaisesRegex(ValueError, "PROMOTE_ARTIFACT_CUSTODY_HOLD"):
                guard._bounded_gh_api(
                    "repos/mbalbo2023/Porota-trading/actions/artifacts/1/zip",
                    max_bytes=4096)
        self.assertEqual(len(spawned), 2)
        self.assertTrue(all(process.poll() is not None for process in spawned))

    def test_gh_cli_exact_64k_boundary_nonzero_exit_and_timeout_are_fail_closed(self):
        original = subprocess.Popen
        endpoint = "repos/mbalbo2023/Porota-trading/actions/artifacts/1/zip"
        scenarios = (("exact", 65536), ("larger", 65537),
                     ("failure", 16), ("timeout", 0))
        for name, count in scenarios:
            with self.subTest(name=name):
                spawned = []
                def fake_gh(argv, **kwargs):
                    code = ("import time;time.sleep(10)" if name == "timeout" else
                            "import sys;sys.stdout.buffer.write(b'Z'*" + str(count) +
                            ");sys.exit(" + ("7" if name == "failure" else "0") + ")")
                    child = original([sys.executable, "-c", code], **kwargs)
                    spawned.append(child)
                    return child
                with patch.object(guard.subprocess, "Popen", side_effect=fake_gh):
                    if name == "exact":
                        self.assertEqual(guard._bounded_gh_api(endpoint, max_bytes=65536),
                                         b"Z" * 65536)
                    elif name == "timeout":
                        with patch.object(guard.select, "select", return_value=([], [], [])):
                            with self.assertRaisesRegex(ValueError, "PROMOTE_ARTIFACT_CUSTODY_HOLD"):
                                guard._bounded_gh_api(endpoint, max_bytes=65536)
                    else:
                        with self.assertRaisesRegex(ValueError, "PROMOTE_ARTIFACT_CUSTODY_HOLD"):
                            guard._bounded_gh_api(endpoint, max_bytes=65536)
                self.assertEqual(len(spawned), 1)
                self.assertIsNotNone(spawned[0].poll(), "stray GH child must be reaped")

    def test_synthetic_forged_artifact_claim_cannot_append_dispatch_command(self):
        claim = {"source_sha": "d" * 40, "source_tree": "e" * 40,
                 "image_id": "sha256:" + "f" * 64,
                 "release_manifest_sha256": "c" * 64,
                 "observer_cid": "1" * 64, "dashboard_cid": "2" * 64,
                 "promote_run_id": 888, "promote_run_attempt": 2,
                 "runtime_artifact_digest": "sha256:" + "3" * 64}
        env_file = self.root / "no_command.env"
        env_file.touch()
        command = "PPI_DIRECT_REST_OPEN_ONCE:2026-11-19:ALL"
        self.assertTrue(guard._append_observer_command_env(
            env_file, command, utc(13, 45, 19), guard.SESSION_REGULAR, claim))
        original = env_file.read_text()
        for key, invalid in (("observer_cid", "0" * 63),
                             ("dashboard_cid", "1" * 64),
                             ("source_sha", "b3cbe57;malicious"),
                             ("promote_run_attempt", True),
                             ("runtime_artifact_digest", "sha256:INVALID")):
            with self.subTest(key=key):
                self.assertFalse(guard._append_observer_command_env(
                    env_file, command, utc(13, 45, 19), guard.SESSION_REGULAR,
                    {**claim, key: invalid}))
                self.assertEqual(env_file.read_text(), original)

    def test_rootless_capture_uses_exact_candidate_tree_image_and_no_rootful_docker(self):
        code = YAML.read_text()
        self.assertIn("RC6_EXPECTED_CANDIDATE_SHA: b3cbe57a5b4b82016a65a0e253b251abbb84a04c", code)
        self.assertIn("RC6_EXPECTED_TREE_SHA: 7370fea6c9c6daa802fcdd8ce6fc20c5cf8e31b0", code)
        self.assertIn("RC6_EXPECTED_IMAGE_ID: sha256:62c649ac94c586f488c516f53a030b75529edd7529425254ff33b2a70bc4008d", code)
        self.assertIn("rootless-capture", code)
        self.assertNotIn("sudo -n docker", code)
        self.assertNotIn("porota_production_observer", code)
        # Calendar and engine object pin gates remain before host and network.
        self.assertIn("rc6_a669_ppi_day_slot_guard.py", code)
        self.assertIn("RC6_A669_ENGINE_BLOB", code)


if __name__ == "__main__":
    unittest.main()
