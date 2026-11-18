"""PPI budget contract regressions; no broker, SDK, or host access."""

from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from rc6_dynamic_universe.promotion import DEFAULT_POLICY, RuntimeCapacityController
from rc6_ppi_global_budget import RuntimePPIBudget


class ArbiterDeadlineContract(unittest.TestCase):
    def test_bounded_capacity_lookup_uses_supported_controller_signature(self):
        with tempfile.TemporaryDirectory(prefix="rc6-ppi-deadline-") as directory:
            database = Path(directory) / "paper.sqlite"
            controller = RuntimeCapacityController(
                database, environ={}, policy=deepcopy(DEFAULT_POLICY))
            budget = RuntimePPIBudget(database, controller)
            self.assertIsNone(budget._current(
                priority="DISCOVERY", deadline=time.monotonic() + 1))
            self.assertFalse(budget.path.exists())

    def test_controller_accepts_absolute_deadline_without_promoting_off_policy(self):
        controller = RuntimeCapacityController(environ={}, policy=deepcopy(DEFAULT_POLICY))
        state = controller.state(deadline=time.monotonic() + 1)
        self.assertEqual(state["status"], "OFF_BASELINE")
        self.assertFalse(state["production_limits_modified"])

    def test_approved_controller_forwards_deadline_to_open_position_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="rc6-ppi-opened-") as directory:
            database = Path(directory) / "paper.sqlite"
            policy = deepcopy(DEFAULT_POLICY)
            policy["mode"] = "APPROVED"
            controller = RuntimeCapacityController(
                database, environ={}, policy=policy)
            cutoff = time.monotonic() + 0.25
            with patch("rc6_dynamic_universe.promotion.resolve_capacity_policy",
                       return_value={"status": "APPROVED_DYNAMIC"}) as resolve, \
                 patch("rc6_ppi_global_budget.supervisable_position_count",
                       return_value=None) as opened:
                state = controller.state(deadline=cutoff)
            resolve.assert_called_once()
            opened.assert_called_once_with(database, deadline=cutoff)
            self.assertEqual(state["status"], "BASELINE_FAIL_CLOSED")
            self.assertEqual(state["reason_codes"], ["CAPACITY_OPENED_LEDGER_UNVERIFIED"])

    def test_uninitialized_exit_observation_reports_missing_budget_not_internal_typeerror(self):
        with tempfile.TemporaryDirectory(prefix="rc6-ppi-exit-") as directory:
            database = Path(directory) / "paper.sqlite"
            controller = RuntimeCapacityController(
                database, environ={}, policy=deepcopy(DEFAULT_POLICY))
            budget = RuntimePPIBudget(database, controller)
            observed = budget.observe_exit_round(
                elapsed_seconds=0, deadline_seconds=2, failures=0)
            self.assertEqual(observed["status"], "BASELINE_NOT_MEASURED")
            self.assertIsNone(observed["lower_suspended"])
            self.assertFalse(budget.path.exists())


if __name__ == "__main__":
    unittest.main()
