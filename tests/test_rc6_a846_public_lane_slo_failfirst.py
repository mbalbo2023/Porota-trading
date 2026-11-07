"""A846 OFFHOST, test-only contract for independent PUBLIC and IOL lanes.

The deliberately RED tests capture *missing* behavior, not permission to
enable a provider, order route, host timer or an unverified SQLite guard.
Existing regression suites already cover calendar, IOL quota, PAPER zero,
atomic source publications and the A840 ABA finding.  Do not duplicate them.

Traceability to A845 issue #732, acceptance cases:
  1 PUBLIC vs slow IOL, 2 SLI/commit time, 3 unavailable source quality,
  4 OAuth independence, 5 single-writer lane, 6 crash/TTL replay,
  7 versioned PUBLIC read, 8 IOL quota, 9 calendar, 10 bounded shutdown,
  11 governed CI, 12 runtime Actions receipts.
Cases 5-12 retain historical owner-scope tests or remain release/host evidence;
these tests cover the orchestration gaps without pretending to certify them.
"""

from datetime import datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scripts import rc6_source_cycle_dispatch as dispatch


REPO = Path(__file__).resolve().parents[1]
ART = ZoneInfo("America/Argentina/Buenos_Aires")
START = datetime(2026, 11, 11, 10, 30, tzinfo=ART)
OUTPUTS = {Path(relative).name: filename
           for _, relative, _, filename in dispatch.STAGES}


def _fixture(tmp_path):
    db = tmp_path / "paper.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE observer_state(id INTEGER, mode TEXT, real_orders_sent INTEGER)")
        connection.execute("INSERT INTO observer_state VALUES(1, 'PRODUCTION_PAPER', 0)")
    market = tmp_path / "market"
    market.mkdir()
    return db, market, tmp_path / "source.lock"


class _ObservedCollector:
    def __init__(self, market, *, public_quality="SCRAPED_PUBLIC_DATA"):
        self.market = market
        self.public_quality = public_quality
        self.calls = []

    def __call__(self, command, **kwargs):
        script = Path(command[1]).name
        self.calls.append(script)
        output = self.market / OUTPUTS[script]
        if script == "rc6_public_source_capture.py":
            value = {"schema": "rc6-consolidated-source-evidence-v1",
                     "collected_at": START.astimezone(timezone.utc).isoformat(),
                     "sources": [{"source": "BYMA", "status": self.public_quality,
                                  "record_count": 1 if self.public_quality == "SCRAPED_PUBLIC_DATA" else 0}]}
        else:
            value = {"schema": "observational-fixture", "real_money_authorized": False}
        staging = output.with_suffix(".new")
        staging.write_text(json.dumps(value), encoding="utf-8")
        os.replace(staging, output)
        return SimpleNamespace(returncode=0,
                               stdout="IOL_SHADOW_COLLECTION=COMPLETE READY=0 OBSERVED=1\n")


def _run(db, market, lock, collector, *, at=START, stage=None):
    """Use an explicit lane if supported; legacy fallback proves RED baseline.

    Avoid pinning the future function's internal parameter spelling: only the
    CLI spelling `--stage` is specified by A845.  The R4 baseline predates
    either internal selector, so its actual global-lock failure is observable.
    """
    kwargs = {"now": at, "db": db, "market_root": market,
              "lock": lock, "runner": collector}
    if stage is not None:
        parameters = inspect.signature(dispatch.run_cycle).parameters
        if "stage" in parameters:
            kwargs["stage"] = stage
        elif "lane" in parameters:
            kwargs["lane"] = stage
    return dispatch.run_cycle(**kwargs)


def _public_service_candidates():
    result = []
    for path in (REPO / "systemd").glob("*.service"):
        content = path.read_text(encoding="utf-8")
        if ("rc6_source_cycle_dispatch.py" in content and
                ("--stage PUBLIC" in content or "--stage public" in content)):
            result.append((path, content))
    return result


def test_public_has_one_independent_minute_timer_not_iol_oneshot():
    """A845-1/5: reuse one dispatcher, two timers, never duplicate PUBLIC."""
    candidates = _public_service_candidates()
    assert len(candidates) == 1, "PUBLIC_LANE_INDEPENDENT_UNIT_MISSING_OR_DUPLICATED"
    service, _ = candidates[0]
    timer = service.with_suffix(".timer")
    assert timer.is_file(), "PUBLIC_LANE_TIMER_MISSING"
    schedule = timer.read_text(encoding="utf-8")
    assert "Unit=" + service.name in schedule
    # Check a *minute cadence*, not merely presence of an annual OnCalendar.
    minute_interval = bool(re.search(r"^OnUnitActiveSec=(60s|1min|1m)$", schedule, re.M))
    market_minute_calendar = (
        "10:30..59:00" in schedule and "11..16:*:00" in schedule)
    assert minute_interval or market_minute_calendar, "PUBLIC_MINUTE_CADENCE_NOT_PROVEN"
    # Systemd's same-unit oneshot must not be shared with 110s IOL.
    assert "porota-iol-shadow-collector-rc6.service" != service.name
    legacy = (REPO / "systemd/porota-iol-shadow-collector-rc6.service").read_text()
    # If the legacy one-shot still runs the combined dispatcher, adding a
    # second timer would create two PUBLIC writers, not solve cadence.
    assert ("--stage COMPLEMENT" in legacy or "--stage complement" in legacy), (
        "LEGACY_IOL_UNIT_STILL_RUNS_COMBINED_PUBLIC_WRITER")
    assert "rc6_public_source_capture.py" not in legacy


def test_public_unit_is_not_conditioned_on_iol_oauth_bootstrap():
    """A845-4: a missing IOL bootstrap file must not suppress PUBLIC."""
    candidates = _public_service_candidates()
    assert len(candidates) == 1, "PUBLIC_LANE_UNIT_MISSING"
    _, service = candidates[0]
    assert "iol_mcp_oauth_bootstrap.json" not in service
    assert "--stage" in service


def test_dispatcher_cli_accepts_single_public_lane_without_running_providers(monkeypatch):
    """A845-1: CLI must route a PUBLIC-only tick using the same dispatcher."""
    routed = []

    def isolated_cycle(**kwargs):
        routed.append(kwargs)
        return {"status": "NOT_DUE", "reason": "OFFHOST_FIXTURE", "stages": {}}

    monkeypatch.setattr(dispatch, "run_cycle", isolated_cycle)
    assert dispatch.main(["--stage", "PUBLIC"]) == 0
    assert len(routed) == 1
    assert str(routed[0].get("stage") or routed[0].get("lane")).upper() == "PUBLIC"


def test_public_due_at_plus_60_is_not_blocked_by_inflight_iol(tmp_path):
    """A845-1: bounded fake IOL block, no 110-second wall-clock sleep."""
    db, market, lock = _fixture(tmp_path)
    iol_entered = threading.Event()
    release_iol = threading.Event()
    errors = []

    class DelayedIol(_ObservedCollector):
        def __call__(self, command, **kwargs):
            if Path(command[1]).name == "rc6_iol_shadow_collect.py":
                iol_entered.set()
                assert release_iol.wait(timeout=3), "FIXTURE_IOL_WAIT_TIMEOUT"
            return super().__call__(command, **kwargs)

    first = DelayedIol(market)
    second = _ObservedCollector(market)

    def first_cycle():
        try:
            _run(db, market, lock, first, stage="COMPLEMENT")
        except Exception as exc:  # retain actual failure while releasing fixture
            errors.append(exc)

    worker = threading.Thread(target=first_cycle, daemon=True)
    worker.start()
    assert iol_entered.wait(timeout=3), "FIXTURE_IOL_STAGE_NOT_ENTERED"
    try:
        second_result = _run(db, market, lock, second,
                             at=START + timedelta(seconds=60), stage="PUBLIC")
    finally:
        release_iol.set()
        worker.join(timeout=3)
    assert not worker.is_alive(), "FIXTURE_ORPHAN_THREAD"
    assert not errors, repr(errors)
    assert "rc6_public_source_capture.py" in second.calls
    assert second_result["stages"]["public"] == "OK"
    assert "rc6_iol_shadow_collect.py" not in second.calls


def test_public_ttl_success_is_stamped_after_actual_publication(tmp_path, monkeypatch):
    """A845-2: TTL counts from publication, regardless of state filename."""
    db, market, lock = _fixture(tmp_path)
    end = START + timedelta(seconds=40)

    class AfterPublicationClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return end.astimezone(tz or timezone.utc)

    monkeypatch.setattr(dispatch, "datetime", AfterPublicationClock)
    first = _ObservedCollector(market)
    result = _run(db, market, lock, first, stage="PUBLIC")
    assert result["status"] == "COMPLETE"
    assert "rc6_public_source_capture.py" in first.calls
    # At t0+60 only 20 seconds elapsed since the t0+40 publication; a correct
    # commit-relative TTL cannot schedule another PUBLIC capture.  We inspect
    # behavior, not legacy state path or proposed new state schema.
    second = _ObservedCollector(market)
    _run(db, market, lock, second,
         at=START + timedelta(seconds=60), stage="PUBLIC")
    assert "rc6_public_source_capture.py" not in second.calls, (
        "PUBLIC_TTL_USES_PRE_CHILD_T0_NOT_POST_COMMIT")


def test_public_unavailable_provider_is_not_reported_as_useful_source(tmp_path):
    """A845-3: file publication is not provider-native source freshness."""
    db, market, lock = _fixture(tmp_path)
    result = _run(db, market, lock,
                  _ObservedCollector(market, public_quality="UNAVAILABLE"))
    assert result["status"] in {"DEGRADED", "COMPLETE"}
    # Keep publication success separate from provider-native availability,
    # without freezing the eventual quality field's internal name/schema.
    represented = json.dumps(result, ensure_ascii=False).upper()
    assert result["status"] != "COMPLETE" or "UNAVAILABLE" in represented or (
        "REFERENCE_ONLY" in represented or "SOURCE_STALE" in represented), (
        "PUBLIC_NATIVE_QUALITY_UNVERIFIED_BUT_REPUBLISH_WAS_TREATED_AS_FRESH")
