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
import shlex
import sqlite3
import threading
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

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
        if any(_selects_stage(command, "PUBLIC")
               for command in _active_directives(content, "Service", "ExecStart")):
            result.append((path, content))
    return result


def _selects_stage(command: str, stage: str) -> bool:
    """Recognize one explicit dispatcher lane, never a substring or shell.

    This fixture supports the canonical Python/script invocation and one selector;
    unsupported systemd/shell expansions fail closed rather than being guessed.
    """
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False
    if arguments[:2] != ["/usr/bin/python3",
                         "/opt/porota-trading/scripts/rc6_source_cycle_dispatch.py"]:
        return False
    return arguments[2:] in (["--stage", stage], ["--stage=" + stage])


def _active_directives(source: str, section: str, directive: str) -> list[str]:
    """Read only live keys of the right systemd section; preserve repeats.

    systemd supports multiple OnCalendar values in one [Timer], so a generic
    strict INI reader would reject valid schedules.  This bounded parser
    intentionally fails closed on unrelated sections and commented entries.
    """
    active, current = [], None
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current == section and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() == directive:
                active.append(value.strip())
    return active


def _duration_seconds(value: str) -> float | None:
    matched = re.fullmatch(r"(\d+(?:\.\d+)?)(us|ms|s|min|h)?", value.strip())
    if not matched:
        return None
    unit = {None: 1, "us": .000001, "ms": .001, "s": 1,
            "min": 60, "h": 3600}[matched.group(2)]
    return float(matched.group(1)) * unit


def _public_quality_context(result: dict) -> bool:
    """A quality reason must belong to PUBLIC/BYMA, not an unrelated IOL."""
    negative = ("UNAVAILABLE", "REFERENCE_ONLY", "SOURCE_STALE")

    def negative_leaf(value):
        if isinstance(value, str):
            state = value.strip().upper()
            return (state in negative or
                    (state.startswith(("PUBLIC_", "BYMA_"))
                     and any(code in state for code in negative)))
        if isinstance(value, dict):
            # Do not descend into metadata, diagnostics or an IOL child; only
            # the PUBLIC node's own status/quality/availability can certify it.
            return any(negative_leaf(value.get(key)) for key in (
                "status", "state", "quality", "availability", "reason",
                "source_quality", "provider_quality") if key in value)
        return False

    for key in ("reason", "status"):
        value = str(result.get(key) or "").strip().upper()
        if value.startswith(("PUBLIC_", "BYMA_")) and any(code in value for code in negative):
            return True
    # Only named source/stage reports are authoritative. An arbitrary
    # metadata.PUBLIC or diagnostics.PUBLIC key is not provider evidence.
    for field in ("source_quality", "provider_quality", "stages", "sources"):
        mapping = result.get(field)
        if isinstance(mapping, dict):
            for key, value in mapping.items():
                if str(key).upper() in {"PUBLIC", "BYMA"} and negative_leaf(value):
                    return True
        elif isinstance(mapping, list):
            for record in mapping:
                if (isinstance(record, dict) and
                        str(record.get("source") or record.get("provider") or "").upper()
                        in {"PUBLIC", "BYMA"} and negative_leaf(record)):
                    return True
    return False


def test_public_has_one_independent_minute_timer_not_iol_oneshot():
    """A845-1/5: reuse one dispatcher, two timers, never duplicate PUBLIC."""
    candidates = _public_service_candidates()
    assert len(candidates) == 1, "PUBLIC_LANE_INDEPENDENT_UNIT_MISSING_OR_DUPLICATED"
    service, service_body = candidates[0]
    assert len(_active_directives(service_body, "Service", "ExecStart")) == 1, (
        "PUBLIC_SERVICE_MULTIPLE_EXECSTART")
    for secondary in ("ExecStartPre", "ExecStartPost"):
        commands = _active_directives(service_body, "Service", secondary)
        assert not any(
            "rc6_public_source_capture.py" in command or
            ("rc6_source_cycle_dispatch.py" in command and "--check-phase" not in command)
            for command in commands), "PUBLIC_SERVICE_SECONDARY_WRITER"
    timer = service.with_suffix(".timer")
    assert timer.is_file(), "PUBLIC_LANE_TIMER_MISSING"
    schedule = timer.read_text(encoding="utf-8")
    # Check a *minute cadence*, not merely presence of an annual OnCalendar.
    active_sec = _active_directives(schedule, "Timer", "OnUnitActiveSec")
    active_cal = _active_directives(schedule, "Timer", "OnCalendar")
    minute_interval = any(value in {"60s", "1min", "1m"} for value in active_sec)
    market_minute_calendar = (any("10:30..59:00" in value for value in active_cal)
                              and any("11..16:*:00" in value for value in active_cal))
    assert minute_interval or market_minute_calendar, "PUBLIC_MINUTE_CADENCE_NOT_PROVEN"
    assert _active_directives(schedule, "Timer", "Unit") == [service.name]
    accuracy = _active_directives(schedule, "Timer", "AccuracySec")
    assert len(accuracy) == 1 and _duration_seconds(accuracy[0]) is not None
    assert _duration_seconds(accuracy[0]) <= 5, "PUBLIC_TIMER_ACCURACY_DEFAULT_JITTER_TOO_LARGE"
    # Systemd's same-unit oneshot must not be shared with 110s IOL.
    assert "porota-iol-shadow-collector-rc6.service" != service.name
    legacy = (REPO / "systemd/porota-iol-shadow-collector-rc6.service").read_text()
    # If the legacy one-shot still runs the combined dispatcher, adding a
    # second timer would create two PUBLIC writers, not solve cadence.
    legacy_exec = _active_directives(legacy, "Service", "ExecStart")
    assert len(legacy_exec) == 1 and _selects_stage(legacy_exec[0], "COMPLEMENT"), (
        "LEGACY_IOL_UNIT_STILL_RUNS_COMBINED_PUBLIC_WRITER")
    assert all("rc6_public_source_capture.py" not in command for command in legacy_exec)


def test_public_unit_is_not_conditioned_on_iol_oauth_bootstrap():
    """A845-4: a missing IOL bootstrap file must not suppress PUBLIC."""
    candidates = _public_service_candidates()
    assert len(candidates) == 1, "PUBLIC_LANE_UNIT_MISSING"
    _, service = candidates[0]
    assert not any("iol_mcp_oauth_bootstrap.json" in item for item in
                   _active_directives(service, "Unit", "ConditionPathExists"))
    assert any(_selects_stage(item, "PUBLIC") for item in
               _active_directives(service, "Service", "ExecStart"))


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
    assert "rc6_public_source_capture.py" not in first.calls, (
        "COMPLEMENT_LANE_MUST_NOT_DUPLICATE_PUBLIC_WRITER")
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
    assert _public_quality_context(result), (
        "PUBLIC_NATIVE_QUALITY_UNVERIFIED_BUT_REPUBLISH_WAS_TREATED_AS_FRESH")


def test_contract_helpers_reject_commented_systemd_and_unrelated_iol_quality():
    """Adversarial negative control of the test harness itself, not source."""
    assert _active_directives("[Service]\n# ExecStart=--stage PUBLIC\nExecCondition=true", "Service", "ExecStart") == []
    assert _active_directives("[Unit]\nOnCalendar=11..16:*:00\n[Timer]\n#OnCalendar=11..16:*:00\nOnCalendar=*-*-01 12:00", "Timer", "OnCalendar") == ["*-*-01 12:00"]
    assert not _public_quality_context({"status": "COMPLETE",
                                        "stages": {"public": "OK", "iol": "UNAVAILABLE"}})
    assert _public_quality_context({"status": "COMPLETE",
                                    "source_quality": {"public": "UNAVAILABLE"}})


def test_commented_stage_cannot_forge_public_unit_or_legacy_split(tmp_path, monkeypatch):
    """The EOD peer's false-GREEN config must be rejected deterministically."""
    units = tmp_path / "systemd"
    units.mkdir()
    public = units / "porota-public-a846.service"
    legacy = units / "porota-iol-shadow-collector-rc6.service"
    timer = units / "porota-public-a846.timer"
    combined = "ExecStart=/usr/bin/python3 /opt/porota-trading/scripts/rc6_source_cycle_dispatch.py\n"
    public.write_text("[Service]\n" + combined + "# --stage PUBLIC\n", encoding="utf-8")
    legacy.write_text("[Service]\n" + combined + "# --stage COMPLEMENT\n", encoding="utf-8")
    timer.write_text("[Timer]\nOnUnitActiveSec=60s\nAccuracySec=5s\nUnit=" + public.name + "\n", encoding="utf-8")
    monkeypatch.setattr(__import__(__name__), "REPO", tmp_path)
    assert _public_service_candidates() == []
    with pytest.raises(AssertionError, match="PUBLIC_LANE_INDEPENDENT_UNIT_MISSING"):
        test_public_has_one_independent_minute_timer_not_iol_oneshot()

    public.write_text("[Service]\n" + combined.rstrip() + " --stage PUBLIC\n", encoding="utf-8")
    assert len(_public_service_candidates()) == 1
    with pytest.raises(AssertionError, match="LEGACY_IOL_UNIT_STILL_RUNS_COMBINED"):
        test_public_has_one_independent_minute_timer_not_iol_oneshot()


def test_wrong_systemd_section_and_default_accuracy_cannot_mint_public_lane(tmp_path, monkeypatch):
    """Live-looking keys under [Unit] are inert; default jitter is 60s."""
    units = tmp_path / "systemd"
    units.mkdir()
    public = units / "porota-public-a846.service"
    timer = units / "porota-public-a846.timer"
    legacy = units / "porota-iol-shadow-collector-rc6.service"
    command = "ExecStart=/usr/bin/python3 /opt/porota-trading/scripts/rc6_source_cycle_dispatch.py"
    public.write_text("[Unit]\n" + command + " --stage PUBLIC\n[Service]\nType=oneshot\n")
    legacy.write_text("[Unit]\n" + command + " --stage COMPLEMENT\n[Service]\nType=oneshot\n")
    timer.write_text("[Unit]\nUnit=" + public.name + "\nOnUnitActiveSec=60s\n[Timer]\nPersistent=false\n")
    monkeypatch.setattr(__import__(__name__), "REPO", tmp_path)
    with pytest.raises(AssertionError):
        test_public_has_one_independent_minute_timer_not_iol_oneshot()


def test_public_ok_with_nested_iol_auth_failure_cannot_claim_public_bad_quality():
    """A foreign IOL error below PUBLIC metadata cannot satisfy PUBLIC SLI."""
    forged = {"status": "COMPLETE",
              "stages": {"public": {"status": "OK",
                                     "metadata": {"iol_oauth": "UNAVAILABLE"}}}}
    assert not _public_quality_context(forged)


@pytest.mark.parametrize("selector, expected", [
    ("--stage PUBLICITY", False),
    ("--stage=PUBLICITY", False),
    ("--stage PUBLIC --stage COMPLEMENT", False),
    ("--STAGE PUBLIC", False),
    ("--stage PUBLIC", True),
    ("--stage=PUBLIC", True),
])
def test_public_stage_suffix_cannot_forge_an_independent_unit(tmp_path, monkeypatch, selector, expected):
    """A substring is not the PUBLIC lane accepted by the dispatcher CLI."""
    units = tmp_path / "systemd"
    units.mkdir()
    (units / "forged.service").write_text(
        "[Service]\nExecStart=/usr/bin/python3 "
        "/opt/porota-trading/scripts/rc6_source_cycle_dispatch.py " + selector + "\n")
    monkeypatch.setattr(__import__(__name__), "REPO", tmp_path)
    assert bool(_public_service_candidates()) is expected


def test_echo_of_dispatcher_command_cannot_forge_public_unit(tmp_path, monkeypatch):
    """A printed Python script name is not an executable PUBLIC collector."""
    units = tmp_path / "systemd"
    units.mkdir()
    (units / "forged.service").write_text(
        "[Service]\nExecStart=/bin/echo "
        "/opt/porota-trading/scripts/rc6_source_cycle_dispatch.py --stage PUBLIC\n")
    monkeypatch.setattr(__import__(__name__), "REPO", tmp_path)
    assert _public_service_candidates() == []


@pytest.mark.parametrize("container", ["metadata", "diagnostics"])
def test_non_authoritative_public_quality_cannot_certify_source_failure(container):
    """An arbitrary PUBLIC key is not a provider-native quality report."""
    assert not _public_quality_context({"status": "COMPLETE",
                                        container: {"PUBLIC": "UNAVAILABLE"}})


def test_multiple_active_execstart_or_pre_post_cannot_duplicate_public(tmp_path, monkeypatch):
    """systemd oneshot permits multiple ExecStart; only one source writer."""
    units = tmp_path / "systemd"
    units.mkdir()
    public = units / "porota-public-a846.service"
    legacy = units / "porota-iol-shadow-collector-rc6.service"
    timer = units / "porota-public-a846.timer"
    dispatcher = "/usr/bin/python3 /opt/porota-trading/scripts/rc6_source_cycle_dispatch.py"
    legacy.write_text("[Service]\nExecStart=" + dispatcher + " --stage COMPLEMENT\n")
    timer.write_text("[Timer]\nUnit=" + public.name + "\nOnUnitActiveSec=60s\nAccuracySec=5s\n")
    monkeypatch.setattr(__import__(__name__), "REPO", tmp_path)

    public.write_text("[Service]\nExecStart=" + dispatcher + " --stage PUBLIC\n"
                      "ExecStart=" + dispatcher + "\n")
    assert len(_public_service_candidates()) == 1
    with pytest.raises(AssertionError, match="PUBLIC_SERVICE_MULTIPLE_EXECSTART"):
        test_public_has_one_independent_minute_timer_not_iol_oneshot()

    public.write_text("[Service]\nExecStart=" + dispatcher + " --stage PUBLIC\n"
                      "ExecStartPost=" + dispatcher + "\n")
    with pytest.raises(AssertionError, match="PUBLIC_SERVICE_SECONDARY_WRITER"):
        test_public_has_one_independent_minute_timer_not_iol_oneshot()
