"""A200: source-cycle success requires a new safe publication, offhost only.

Providers remain fully stubbed. No broker calls, host writes or market traffic.
"""

from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from scripts import rc6_source_cycle_dispatch as dispatch


ART = ZoneInfo("America/Argentina/Buenos_Aires")
OUTPUTS = {Path(relative).name: output for _, relative, _, output in dispatch.STAGES}


class Collector:
    """Return a success signal; independently choose which files are written."""

    def __init__(self, root: Path, publishing=()):
        self.root = root
        self.publishing = frozenset(publishing)
        self.calls = []

    def __call__(self, command, **kwargs):
        name = Path(command[1]).name
        self.calls.append(name)
        if name in self.publishing:
            target = self.root / OUTPUTS[name]
            temporary = target.with_suffix(target.suffix + ".new")
            temporary.write_text('{"same":"bytes"}', encoding="utf-8")
            os.replace(temporary, target)
        return SimpleNamespace(returncode=0,
                               stdout="IOL_SHADOW_COLLECTION=COMPLETE READY=1 OBSERVED=1\n")


class SourceCyclePublicationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="porota-a200-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.market = self.root / "market"
        self.market.mkdir()
        self.db = self.root / "paper.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE observer_state(id INTEGER, mode TEXT, real_orders_sent INTEGER)")
            connection.execute("INSERT INTO observer_state VALUES(1, 'PRODUCTION_PAPER', 0)")

    def cycle(self, publisher, minute=30):
        return dispatch.run_cycle(now=datetime(2026, 10, 28, 10, minute, tzinfo=ART),
                                  db=self.db, market_root=self.market,
                                  lock=self.root / "source.lock", runner=publisher)

    def old_outputs(self):
        for output in OUTPUTS.values():
            (self.market / output).write_text('{"same":"bytes"}', encoding="utf-8")

    def test_exit_zero_and_old_outputs_cannot_fake_completed_cycle(self):
        self.old_outputs()
        collector = Collector(self.market)
        result = self.cycle(collector)
        self.assertEqual(result["status"], "DEGRADED")
        self.assertEqual(set(result["stages"].values()), {"OUTPUT_NOT_REPUBLISHED"})
        self.assertFalse((self.market / "rc6_source_cycle_dispatch_state.json").exists())
        self.assertTrue(all((self.market / output).read_text(encoding="utf-8") ==
                            '{"same":"bytes"}' for output in OUTPUTS.values()))
        self.assertEqual(len(collector.calls), 3)

    def test_same_bytes_atomically_republished_are_accepted(self):
        self.old_outputs()
        collector = Collector(self.market, publishing=OUTPUTS)
        first = self.cycle(collector)
        self.assertEqual(first["status"], "COMPLETE")
        self.assertEqual(set(first["stages"].values()), {"OK"})
        second = self.cycle(collector, minute=31)
        self.assertEqual(second["status"], "COMPLETE")
        self.assertEqual(second["stages"]["evidence"], "NOT_DUE_TTL")
        self.assertEqual(second["stages"]["public"], "OK")
        self.assertEqual(second["stages"]["iol"], "OK")

    def test_unpublished_evidence_does_not_advance_its_ttl(self):
        self.old_outputs()
        collector = Collector(self.market, publishing=(
            "rc6_iol_shadow_collect.py", "rc6_public_source_capture.py"))
        first = self.cycle(collector)
        self.assertEqual(first["status"], "DEGRADED")
        self.assertEqual(first["stages"]["evidence"], "OUTPUT_NOT_REPUBLISHED")
        state = self.market / "rc6_source_cycle_dispatch_state.json"
        self.assertIn('"last_public_success_at"', state.read_text(encoding="utf-8"))
        self.assertIn('"last_evidence_success_at":null', state.read_text(encoding="utf-8"))
        collector.calls.clear()
        second = self.cycle(collector, minute=31)
        self.assertEqual(second["stages"]["evidence"], "OUTPUT_NOT_REPUBLISHED")
        self.assertIn("rc6_full_universe_evidence.py", collector.calls)

    def test_unsafe_preexisting_output_blocks_before_child_runs(self):
        target = self.root / "foreign.json"
        target.write_text('{"protected":true}', encoding="utf-8")
        (self.market / "iol_shadow_latest.json").symlink_to(target)
        collector = Collector(self.market, publishing=OUTPUTS)
        with self.assertRaisesRegex(dispatch.SourceCycleBlocked, "SOURCE_CYCLE_OUTPUT_UNSAFE"):
            self.cycle(collector)
        self.assertEqual(collector.calls, [])
        self.assertEqual(target.read_text(encoding="utf-8"), '{"protected":true}')

    def test_hardlinked_output_blocks_before_child_runs(self):
        target = self.root / "foreign.json"
        target.write_text('{"protected":true}', encoding="utf-8")
        os.link(target, self.market / "iol_shadow_latest.json")
        collector = Collector(self.market, publishing=OUTPUTS)
        with self.assertRaisesRegex(dispatch.SourceCycleBlocked, "SOURCE_CYCLE_OUTPUT_UNSAFE"):
            self.cycle(collector)
        self.assertEqual(collector.calls, [])


if __name__ == "__main__":
    unittest.main()
