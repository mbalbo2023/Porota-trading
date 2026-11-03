"""A542: complementary IOL OAuth does not gate PAPER public/evidence.

All producers are fake subprocesses. No provider, droplet or credential calls.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from scripts import rc6_source_cycle_dispatch as dispatch


ART = ZoneInfo("America/Argentina/Buenos_Aires")
OUTPUTS = {Path(script).name: filename for _, script, _, filename in dispatch.STAGES}


def _run(tmp_path, writer, *, minute=30, bootstrap=None):
    return dispatch.run_cycle(
        now=datetime(2026, 10, 28, 10, minute, tzinfo=ART),
        db=tmp_path / "paper.db", market_root=tmp_path / "market",
        lock=tmp_path / "source.lock", runner=writer,
        iol_bootstrap=bootstrap or tmp_path / "missing-iol-bootstrap.json",
    )


class FakeWriter:
    def __init__(self, market, *, fail=()):
        self.market = market
        self.fail = frozenset(fail)
        self.calls = []

    def __call__(self, command, **kwargs):
        assert kwargs["timeout"] <= 110
        assert kwargs["capture_output"] and kwargs["text"]
        name = Path(command[1]).name
        self.calls.append(name)
        if name in self.fail:
            return SimpleNamespace(returncode=1, stdout="")
        target = self.market / OUTPUTS[name]
        temporary = target.with_suffix(".new")
        temporary.write_text('{"published":true}', encoding="utf-8")
        os.replace(temporary, target)
        return SimpleNamespace(returncode=0,
                               stdout="IOL_SHADOW_COLLECTION=COMPLETE READY=1 OBSERVED=1\n")


class IolOptionalSourceCycleTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rc6-a542-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        (self.workspace / "market").mkdir()
        with sqlite3.connect(self.workspace / "paper.db") as conn:
            conn.execute("CREATE TABLE observer_state(id INT, mode TEXT, real_orders_sent INT)")
            conn.execute("INSERT INTO observer_state VALUES(1, 'PRODUCTION_PAPER', 0)")

    def test_missing_iol_bootstrap_skips_only_complement_and_publishes_sources(self):
        workspace = self.workspace
        writer = FakeWriter(workspace / "market")
        first = _run(workspace, writer)
        assert first["status"] == "DEGRADED"  # No silent green for absent IOL.
        assert first["real_orders_sent"] == 0
        assert first["stages"] == {
            "iol": "COMPLEMENT_AUTH_UNAVAILABLE", "public": "OK", "evidence": "OK"}
        assert writer.calls == ["rc6_public_source_capture.py", "rc6_full_universe_evidence.py"]
        for filename in ("rc6_public_sources_latest.json", "rc6_instrument_evidence_latest.json"):
            assert (workspace / "market" / filename).is_file()
        assert not (workspace / "market" / "iol_shadow_latest.json").exists()
        state = json.loads((workspace / "market" / "rc6_source_cycle_dispatch_state.json").read_text())
        assert state["last_public_success_at"] and state["last_evidence_success_at"]

        writer.calls.clear()
        second = _run(workspace, writer, minute=31)
        assert second["stages"] == {
            "iol": "COMPLEMENT_AUTH_UNAVAILABLE", "public": "OK", "evidence": "NOT_DUE_TTL"}
        assert writer.calls == ["rc6_public_source_capture.py"]

    def test_present_regular_bootstrap_preserves_all_existing_stages(self):
        bootstrap = self.workspace / "iol_bootstrap.json"
        bootstrap.write_text("{}", encoding="utf-8")
        writer = FakeWriter(self.workspace / "market")
        result = _run(self.workspace, writer, bootstrap=bootstrap)
        assert result["status"] == "COMPLETE"
        assert result["stages"] == {"iol": "OK", "public": "OK", "evidence": "OK"}
        assert writer.calls == [Path(script).name for _, script, _, _ in dispatch.STAGES]

    def test_bootstrap_symlink_cannot_enable_iol_but_public_continues(self):
        real_file = self.workspace / "private-oauth.json"
        real_file.write_text("not used", encoding="utf-8")
        alias = self.workspace / "alias-oauth.json"
        alias.symlink_to(real_file)
        writer = FakeWriter(self.workspace / "market")
        result = _run(self.workspace, writer, bootstrap=alias)
        assert result["stages"] == {
            "iol": "COMPLEMENT_AUTH_UNAVAILABLE", "public": "OK", "evidence": "OK"}
        assert "rc6_iol_shadow_collect.py" not in writer.calls

    def test_failed_public_does_not_advance_ttl_or_suppress_retry(self):
        writer = FakeWriter(self.workspace / "market", fail={"rc6_public_source_capture.py"})
        first = _run(self.workspace, writer)
        assert first["stages"] == {
            "iol": "COMPLEMENT_AUTH_UNAVAILABLE", "public": "FAILED", "evidence": "OK"}
        state = json.loads((self.workspace / "market" / "rc6_source_cycle_dispatch_state.json").read_text())
        assert state["last_public_success_at"] is None
        assert state["last_evidence_success_at"]
        writer.calls.clear()
        second = _run(self.workspace, writer, minute=31)
        assert second["stages"]["public"] == "FAILED"
        assert second["stages"]["evidence"] == "NOT_DUE_TTL"
        assert writer.calls == ["rc6_public_source_capture.py"]

    def test_paper_zero_gate_remains_before_any_stage(self):
        with sqlite3.connect(self.workspace / "paper.db") as conn:
            conn.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
        writer = FakeWriter(self.workspace / "market")
        with self.assertRaisesRegex(dispatch.SourceCycleBlocked, "PAPER_ZERO_REQUIRED"):
            _run(self.workspace, writer)
        assert writer.calls == []

    def test_unit_has_no_global_iol_condition_and_preserves_timer_budget(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "systemd/porota-iol-shadow-collector-rc6.service").read_text()
        timer = (root / "systemd/porota-iol-shadow-collector-rc6.timer").read_text()
        oauth_health = (root / "systemd/porota-iol-oauth-health-rc6.service").read_text()
        assert "ConditionPathExists=" not in service
        assert "TimeoutStartSec=210s" in service
        assert "10:30..59:00 America/Argentina/Buenos_Aires" in timer
        assert "AccuracySec=5s" in timer
        assert "ConditionPathExists=/root/.config/porota/iol_mcp_oauth_bootstrap.json" in oauth_health
        assert [row[2] for row in dispatch.STAGES] == [110, 50, 25]
        assert dispatch.PUBLIC_TTL_SECONDS == 60
        assert dispatch.EVIDENCE_TTL_SECONDS == 300


if __name__ == "__main__":
    unittest.main()
