"""Wrapper contracts only: no physical1201-tick horizon is executed here.

Controlled clocks and small metadata negatives test the measurement boundary;
their PASS can never replace the separate native wheel/GC/storage execution.
"""
from pathlib import Path
import runpy

import pytest


@pytest.fixture
def probe():
    return runpy.run_path(str(Path(__file__).resolve().parents[1]/
        "docs/audits/rc6-convergence-persistence-evidence/native_archive_v3_profile_probe_v2.py"))


def test_restart_factory_recovery_is_charged_to_the_original_full_cycle_clock(probe, monkeypatch, record_property):
    record_property("evidence_scope", "CONTROLLED_WRAPPER_CLOCK_NOT_NATIVE_HORIZON")
    clock = [100.]
    monkeypatch.setattr(probe["time"], "monotonic", lambda: clock[0])
    monkeypatch.setattr(probe["time"], "process_time", lambda: 10.)
    worker, rebuilt, restarts = object(), object(), []
    def factory():
        clock[0] += 31.
        return rebuilt
    actual, started, cpu = probe["start_cycle"](worker, index=361, ticks=1201, factory=factory, restarts=restarts)
    assert actual is rebuilt and restarts == [361] and cpu == 10.
    # Constructor/recovery alone has already exhausted the existing30s.
    assert started == 100. and clock[0]-started == 31.


def test_non_restart_cycle_does_not_construct_a_new_worker_or_change_scope(probe, monkeypatch, record_property):
    record_property("evidence_scope", "CONTROLLED_WRAPPER_CLOCK_NOT_NATIVE_HORIZON")
    monkeypatch.setattr(probe["time"], "monotonic", lambda: 100.)
    worker, restarts = object(), []
    actual, started, _ = probe["start_cycle"](worker, index=1, ticks=1201,
        factory=lambda: pytest.fail("Non-restart constructed worker"), restarts=restarts)
    assert actual is worker and started == 100. and restarts == []


@pytest.mark.parametrize("ticks,executed", ((4,4),(1201,4),(1201,1201)))
def test_four_cuts_or_an_incomplete_wheel_cannot_claim_horizon_complete(probe, ticks, executed, record_property):
    record_property("evidence_scope", "METADATA_ONLY_NO1201_NATIVE_WHEEL_EXECUTION")
    flags = probe["completion_flags"]({"cuts":[{}]*executed,"ticks_requested":ticks,
        "execution_complete":True,"source_database_unchanged":True,"code_source_unchanged":True,"provider_requests":0})
    assert flags["execution_complete"] is True
    assert flags["horizon_complete"] is flags["complete"] is flags["acceptance_complete"] is False


@pytest.mark.parametrize("missing", ("projection.sqlite","manifest.json"))
def test_equal_legacy_or_incomplete_member_snapshots_are_rejected(probe, missing, record_property):
    record_property("evidence_scope", "MEMBER_SET_CONTRACT_NOT_NATIVE_ARCHIVE_RECOVERY")
    members = {name:b"original" for name in probe["ORIGINAL_MEMBERS"] if name != missing}
    # Snapshot==restore equality would have accepted the old four-member shape.
    assert members == dict(members)
    with pytest.raises(AssertionError, match="EXACT_FIVE_ORIGINAL_MEMBERS_REQUIRED"):
        probe["exact_original_members"](members)


def test_current_five_member_shape_is_preserved_as_metadata_only_control(probe, record_property):
    record_property("evidence_scope", "MEMBER_SET_CONTRACT_NOT_NATIVE_ARCHIVE_RECOVERY")
    probe["exact_original_members"]({name:b"original" for name in probe["ORIGINAL_MEMBERS"]})
