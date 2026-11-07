"""A796 OFFHOST safety regression: no provider path without durable CAS.

The tested break is accepting existing credentials and a budget as sufficient
to import the PPI reader, even when no A689 per-day slot has been proven.
No provider module, socket, runtime filesystem, or credential is opened.
"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import base64
import json
import os
import sys
import unittest

from scripts import rc6_a630_host_ppi_current_book as engine


class OffhostProviderAdmission(unittest.TestCase):
    def test_main_scheduler_and_dispatch_cannot_start_provider_job_without_new_code(self):
        source = Path(".github/workflows/rc6-a669-ppi-current-book-20261105.yml").read_text()
        provider_job = source.split("  sample_main_only:", 1)[1]
        gate = provider_job.split("    # A diagnostic", 1)[0]
        self.assertIn("false &&", gate)
        self.assertIn("github.event_name == 'workflow_dispatch'", gate)
        self.assertIn("github.sha == 'e2e56e85aa98b4f7e45268e2a8a21f61f6d1c05b'", gate)
        self.assertNotIn("github.event_name == 'schedule'", gate)

    def test_missing_durable_slot_denies_before_reader_and_capture(self):
        target = engine.SLOTS["1045"]
        encoded = base64.b64encode(json.dumps(target).encode()).decode()
        fake_modules = {
            "_version": SimpleNamespace(
                MODE="PRODUCTION_PAPER", REAL_ORDER_CAPABILITY="BLOCKED"
            ),
            "bd_ppi_readonly_guard": SimpleNamespace(
                ProductionMarketReader=object()
            ),
        }
        env = {
            "RC6_A630_EXPECTED_PRODUCT_SHA": engine.PRODUCT_SHA,
            "RC6_A630_TARGET_B64": encoded,
        }
        buffer = StringIO()
        with patch.dict(sys.modules, fake_modules), patch.dict(os.environ, env, clear=True):
            with patch.object(engine, "existing_container_keypair", return_value=("fake", "fake")):
                with patch.object(engine, "_existing_shared_budget", return_value=(object(), None)):
                    with patch.object(engine, "capture_two") as capture:
                        with redirect_stdout(buffer):
                            rc = engine.container_main()
        self.assertEqual(rc, 2)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["reason"], "A689_DURABLE_SLOT_CAS_HOLD")
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
