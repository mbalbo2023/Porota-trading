"""A716 R2: PPI budget selector boot bridge on exact R1 code (no provider/host I/O)."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import porota_mode_manager as manager


class BudgetBootBridgeR2Tests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"
        (self.data / "diagnosticos").mkdir(parents=True)
        p = patch.object(manager, "DATA", self.data)
        p.start()
        self.addCleanup(p.stop)

    def _observer(self, config):
        with patch.object(manager, "env_file", return_value=dict(config)):
            return manager.observer_runtime_env()

    def test_absent_selectors_preserve_existing_observer_environment(self):
        file_path = self._observer({})
        text = file_path.read_text(encoding="utf-8")
        fields = dict(line.split("=", 1) for line in text.splitlines())
        self.assertEqual(file_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(fields["PAPER_T1_FULL_DATE_RELEASE"], "true")
        self.assertEqual(fields["PAPER_RISK_PER_TRADE"], manager.paper_settings({})["PAPER_RISK_PER_TRADE"])
        self.assertEqual(fields["TELEGRAM_BOT_TOKEN"], "")
        self.assertEqual(fields["TELEGRAM_CHAT_ID"], "")
        for key in manager.PPI_BUDGET_OBSERVER_SELECTORS:
            self.assertNotIn(key, fields)
        self.assertFalse(any(p.name == "global.sqlite" for p in self.data.rglob("*")))

    def test_seven_explicit_non_secret_selectors_reach_observer_only(self):
        paths = {
            "POROTA_CAPACITY_POLICY_PATH": "/app/ops/policy/rc6-dynamic-capacity-v1.json",
            "POROTA_CAPACITY_REPORT_PATH": "/app/data/ppi-capacity/report.json",
            "POROTA_CAPACITY_RECOMMENDATION_PATH": "/app/data/ppi-capacity/recommendation.json",
            "POROTA_CAPACITY_APPROVAL_PATH": "/app/operator/ppi-capacity/approval.json",
            "POROTA_CAPACITY_SHADOW_PATH": "/app/data/ppi-capacity/latest.json.gz",
            "POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH": "/app/operator/ppi-capacity/shared-v2.json",
        }
        secrets = {"PPI_API_KEY": "DO_NOT_FORWARD_TOKEN",
                   "POROTA_BUILD_SHA": "DO_NOT_FORWARD_BUILD",
                   "POROTA_UNREVIEWED_EXTRA_PATH": "/app/never-forward"}
        self.assertEqual(len(manager.PPI_BUDGET_OBSERVER_SELECTORS), 7)
        file_path = self._observer({**paths, **secrets,
                                    "POROTA_DYNAMIC_CAPACITY_MODE": "approved"})
        text = file_path.read_text(encoding="utf-8")
        fields = dict(line.split("=", 1) for line in text.splitlines())
        self.assertEqual(fields["POROTA_DYNAMIC_CAPACITY_MODE"], "APPROVED")
        for key, value in paths.items():
            self.assertEqual(fields[key], value)
        self.assertEqual(file_path.stat().st_mode & 0o777, 0o600)
        for key, value in secrets.items():
            self.assertNotIn(key, fields)
            self.assertNotIn(value, text)
        self.assertFalse(any(p.name == "global.sqlite" for p in self.data.rglob("*")))

        preview = self.data / "diagnosticos/dashboard_preview_v1633.env"
        preview.write_text("DASHBOARD_ACCESS_TOKEN=synthetic\n" +
            "\n".join(f"{key}={value}" for key, value in paths.items()) +
            "\nPOROTA_DYNAMIC_CAPACITY_MODE=APPROVED\nPPI_API_KEY=DO_NOT_LEAK\n",
            encoding="utf-8")
        with patch.object(manager, "env_file", return_value={}):
            dash = manager.dashboard_env("PRODUCTION_PAPER")
        dashboard_text = dash.read_text(encoding="utf-8")
        self.assertIn("DASHBOARD_ACCESS_TOKEN=synthetic\n", dashboard_text)
        for key in manager.PPI_BUDGET_OBSERVER_SELECTORS:
            self.assertNotIn(key + "=", dashboard_text)
        self.assertNotIn("DO_NOT_LEAK", dashboard_text)

    def test_blank_selectors_are_ignored_and_mode_is_normalized(self):
        text = self._observer({
            "POROTA_DYNAMIC_CAPACITY_MODE": " shadow ",
            "POROTA_CAPACITY_APPROVAL_PATH": "  ",
        }).read_text(encoding="utf-8")
        self.assertIn("POROTA_DYNAMIC_CAPACITY_MODE=SHADOW\n", text)
        self.assertNotIn("POROTA_CAPACITY_APPROVAL_PATH=", text)

    def test_invalid_selector_fails_before_stop_or_runtime_write(self):
        cases = (
            ({"POROTA_DYNAMIC_CAPACITY_MODE": "LIVE"}, "RC6_PPI_BUDGET_MODE_INVALID"),
            ({"POROTA_CAPACITY_APPROVAL_PATH": "./relative.json"}, "RC6_PPI_BUDGET_PATH_NOT_ABSOLUTE"),
            ({"POROTA_CAPACITY_REPORT_PATH": "/app/file\nPPI_API_KEY=secret"}, "RC6_PPI_BUDGET_SELECTOR_INVALID"),
            ({"POROTA_CAPACITY_POLICY_PATH": "/app/file\x00bad"}, "RC6_PPI_BUDGET_SELECTOR_INVALID"),
            ({"POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH": "/app/file\rE=1"}, "RC6_PPI_BUDGET_SELECTOR_INVALID"),
            ({"POROTA_DYNAMIC_CAPACITY_MODE": 123}, "RC6_PPI_BUDGET_SELECTOR_INVALID"),
        )
        output = self.data / "diagnosticos/observer_runtime_v17.env"
        for config, reason in cases:
            with self.subTest(reason=reason, config=list(config)):
                output.unlink(missing_ok=True)
                with (
                    patch.object(manager, "env_file", return_value=config),
                    patch.object(manager, "simulation_image", return_value="synthetic-image"),
                    patch.object(manager, "stop_engines") as stop,
                    patch.object(manager, "run") as command,
                ):
                    with self.assertRaisesRegex(RuntimeError, reason):
                        manager.simulation()
                    stop.assert_not_called()
                    command.assert_not_called()
                self.assertFalse(output.exists())
