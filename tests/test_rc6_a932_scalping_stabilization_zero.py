"""Execute the real Deploy V2 Python subgate, never SSH or shell commands."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/porota-deploy-v2-promote.yml"


def gate_source():
    source = WORKFLOW.read_text(encoding="utf-8")
    marker = 'SCALPING_GATE="$(sudo -n timeout 10 python3 - '
    assert source.count(marker) == 1
    block = source.split(marker, 1)[1].split("<<'PY'\n", 1)[1]
    return textwrap.dedent(block.split("\n          PY", 1)[0])


class ScalpingStabilizationZeroTest(unittest.TestCase):
    def run_gate(self, worker=0, observer=0, *, mode="PRODUCTION_PAPER",
                 state="RUNNING", failed=0, successful=1, heartbeat_seconds=0,
                 missing=None):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory(prefix="rc6-a932-") as directory:
            database = Path(directory) / "synthetic.db"
            with sqlite3.connect(database) as connection:
                # No affinity: preserve TEXT/REAL/NULL/BLOB corruption at the
                # consumer boundary instead of SQLite coercing it for the test.
                connection.execute("CREATE TABLE intraday_scalping_worker_state "
                                   "(id,heartbeat_at,state,successful,failed,real_orders_sent)")
                connection.execute("CREATE TABLE observer_state (id,mode,real_orders_sent)")
                if missing != "worker":
                    connection.execute("INSERT INTO intraday_scalping_worker_state VALUES(1,?,?,?,?,?)",
                                       ((now + timedelta(seconds=heartbeat_seconds)).isoformat(),
                                        state, successful, failed, worker))
                if missing != "observer":
                    connection.execute("INSERT INTO observer_state VALUES(1,?,?)", (mode, observer))
            before = database.read_bytes()
            result = subprocess.run([sys.executable, "-c", gate_source(), str(database),
                                     (now - timedelta(seconds=60)).isoformat()],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(database.read_bytes(), before)
            return result.stdout

    def test_integer_zero_is_green_for_both_healthy_states(self):
        for state in ("RUNNING", "WAITING_MARKET"):
            with self.subTest(state=state):
                self.assertTrue(self.run_gate(state=state).startswith("GREEN|"))

    def test_each_counter_rejects_non_integer_zero_and_nonzero(self):
        for owner in ("worker", "observer"):
            for counter in ("0", "00", " 0 ", 0.0, 0.5, None, 1, -1, b"0"):
                with self.subTest(owner=owner, counter=repr(counter)):
                    self.assertTrue(self.run_gate(**{owner: counter}).startswith("WAIT|"))

    def test_other_admission_guards_remain_effective(self):
        for options in ({"mode": "REAL"}, {"state": "DEGRADED"}, {"failed": 1},
                        {"heartbeat_seconds": -301}, {"heartbeat_seconds": 60},
                        {"heartbeat_seconds": -90}, {"missing": "worker"},
                        {"missing": "observer"}):
            with self.subTest(options=options):
                self.assertTrue(self.run_gate(**options).startswith("WAIT|"))

    def test_worker_outcome_counts_are_trustworthy_nonnegative_integers(self):
        for field in ("failed", "successful"):
            for value in ("0", 0.0, None, -1):
                with self.subTest(field=field, value=value):
                    self.assertTrue(self.run_gate(**{field: value}).startswith("WAIT|"))
        self.assertTrue(self.run_gate(successful=0).startswith("GREEN|"))

    def test_python_boolean_is_not_integer_zero_in_actual_gate_expression(self):
        # sqlite3 persists bool as INTEGER, so do not pretend a SQLite fixture
        # can preserve bool. Exercise the unmodified production expression.
        tree = ast.parse(gate_source())
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "healthy"
                                  for target in node.targets))
        expression = compile(ast.Expression(assignment.value), str(WORKFLOW), "eval")
        baseline = dict(healthy_state=True, failed=0, successful=1, age=0,
                        post_gate_heartbeat=True, worker_orders=0,
                        observer_mode="PRODUCTION_PAPER", observer_orders=0)
        self.assertIs(eval(expression, {}, baseline), True)
        for owner in ("worker_orders", "observer_orders", "failed", "successful"):
            for value in (False, True):
                with self.subTest(owner=owner, value=value):
                    self.assertIs(eval(expression, {}, baseline | {owner: value}), False)


if __name__ == "__main__":
    unittest.main()
