"""A829: safe PUBLIC cadence and all-target publication preflight, OFFHOST.

No live provider, IOL OAuth, systemd, droplet or PAPER broker operation.
"""
from datetime import datetime
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts import rc6_source_cycle_dispatch as dispatch


ART = ZoneInfo("America/Argentina/Buenos_Aires")
OUTPUTS = {Path(relative).name: output for _, relative, _, output in dispatch.STAGES}


def _fixture(tmp_path):
    db = tmp_path / "paper.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE observer_state(id INT, mode TEXT, real_orders_sent INT)")
        connection.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
    market_root = tmp_path / "market"
    market_root.mkdir()
    return db, market_root


class _SafeRunner:
    def __init__(self, market_root):
        self.market_root = market_root
        self.calls = []

    def __call__(self, command, **kwargs):
        script = Path(command[1]).name
        self.calls.append(script)
        path = self.market_root / OUTPUTS[script]
        tmp = path.with_suffix(".tmp")
        tmp.write_text('{}', encoding='utf-8')
        os.replace(tmp, path)
        return SimpleNamespace(returncode=0,
                               stdout="IOL_SHADOW_COLLECTION=COMPLETE READY=1 OBSERVED=1\n")


def _cycle(tmp_path, db, market_root, runner, now=None):
    return dispatch.run_cycle(
        now=now or datetime(2026, 11, 5, 10, 30, tzinfo=ART),
        db=db, market_root=market_root, lock=tmp_path / "source.lock",
        runner=runner,
    )


def test_public_60_second_stage_is_always_before_potentially_110_second_iol(tmp_path):
    db, market_root = _fixture(tmp_path)
    runner = _SafeRunner(market_root)
    first = _cycle(tmp_path, db, market_root, runner)
    assert first["status"] == "COMPLETE"
    assert runner.calls == ["rc6_public_source_capture.py",
                            "rc6_iol_shadow_collect.py",
                            "rc6_full_universe_evidence.py"]
    runner.calls.clear()
    second = _cycle(tmp_path, db, market_root, runner,
                    datetime(2026, 11, 5, 10, 31, tzinfo=ART))
    assert second["status"] == "COMPLETE"
    assert runner.calls == ["rc6_public_source_capture.py",
                            "rc6_iol_shadow_collect.py"]


@pytest.mark.parametrize("unsafe_stage", ["iol", "public", "evidence"])
@pytest.mark.parametrize("alias_type", ["symlink", "hardlink"])
def test_later_stage_alias_rejected_before_any_child(tmp_path, unsafe_stage, alias_type):
    db, market_root = _fixture(tmp_path)
    runner = _SafeRunner(market_root)
    protected = tmp_path / "other_service_output.json"
    protected.write_text('{"owner":"another app"}', encoding="utf-8")
    output = next(output for name, _, _, output in dispatch.STAGES
                  if name == unsafe_stage)
    if alias_type == "symlink":
        (market_root / output).symlink_to(protected)
    else:
        os.link(protected, market_root / output)

    with pytest.raises(dispatch.SourceCycleBlocked,
                       match="SOURCE_CYCLE_OUTPUT_UNSAFE"):
        _cycle(tmp_path, db, market_root, runner)
    assert runner.calls == []
    assert protected.read_text(encoding="utf-8") == '{"owner":"another app"}'
    assert not (market_root / "rc6_source_cycle_dispatch_state.json").exists()


def test_late_t1_cycle_does_not_widen_public_or_evidence_or_iol_calls(tmp_path):
    db, market_root = _fixture(tmp_path)
    runner = _SafeRunner(market_root)
    result = _cycle(tmp_path, db, market_root, runner,
                    datetime(2026, 11, 5, 17, 30, tzinfo=ART))
    assert result["status"] == "COMPLETE"
    assert runner.calls == ["rc6_iol_shadow_collect.py"]
    assert result["stages"] == {"iol": "OK"}


def test_alias_appearing_between_stages_is_not_passed_to_next_child(tmp_path):
    db, market_root = _fixture(tmp_path)
    protected = tmp_path / "other_service_output.json"
    protected.write_text('{"owner":"another app"}', encoding="utf-8")

    class AliasInjectingRunner(_SafeRunner):
        def __call__(self, command, **kwargs):
            script = Path(command[1]).name
            if script == "rc6_iol_shadow_collect.py":
                pytest.fail("An output alias reached the complementary provider child")
            result = super().__call__(command, **kwargs)
            if script == "rc6_public_source_capture.py":
                (market_root / "iol_shadow_latest.json").symlink_to(protected)
            return result

    runner = AliasInjectingRunner(market_root)
    with pytest.raises(dispatch.SourceCycleBlocked,
                       match="SOURCE_CYCLE_OUTPUT_(UNSAFE|CHANGED_BEFORE_CHILD)"):
        _cycle(tmp_path, db, market_root, runner)
    assert runner.calls == ["rc6_public_source_capture.py"]
    assert protected.read_text(encoding="utf-8") == '{"owner":"another app"}'
