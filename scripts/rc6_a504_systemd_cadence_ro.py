#!/usr/bin/env python3
"""A504: bounded, metadata-only evidence of *recent* RC6 systemd cadence.

This is not a full-session certification or an EOD/source-quality verifier.
Never read journal MESSAGE, provider data, market quotes, SQLite, or secrets.
Every positive statement is limited to two mature nominal slots and distinct
service-invocation metadata. Missing/incomplete telemetry fails closed.
"""

from __future__ import annotations

from datetime import date, datetime, time as wall_time, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from zoneinfo import ZoneInfo


ART = ZoneInfo("America/Argentina/Buenos_Aires")
SCHEMA = "rc6.a504.systemd-cadence-readonly.v2"
SOURCE_STATE = Path("/opt/porota-trading/data/market/rc6_source_cycle_dispatch_state.json")
# Fixed producer contract (rc6_source_cycle_dispatch.py). These two clocks
# describe PUBLIC and EVIDENCE stages; neither proves authenticated PPI Book,
# IOL availability, provider budget or instrument readiness.
SOURCE_STATE_SCHEMA = "rc6.source-cycle-dispatch.v1"
SOURCE_STAGE_TTLS = (("last_public_success_at", 60),
                     ("last_evidence_success_at", 5 * 60))
LOOKBACK_MINUTES = 90
MATURE_GRACE_SECONDS = 120
MAX_JOURNAL_BYTES = 128 * 1024
MAX_JOURNAL_RECORDS = 1500
INVOCATION = re.compile(r"^[0-9a-f]{32}$")
JOURNAL_METADATA_STATES = frozenset({
    "UNAVAILABLE", "NOT_COLLECTED", "NO_JOURNAL_ROWS",
    "UNIT_FIELD_UNAVAILABLE", "INVOCATION_ID_UNAVAILABLE",
    "REALTIME_TIMESTAMP_UNAVAILABLE", "MATCHING_NOT_EVALUATED",
    "NO_NOMINAL_SLOT_MATCH",
    "MATCHED_NOMINAL_SLOTS",
})
JOURNAL_COUNT_FIELDS = (
    "journal_records", "journal_unit_records",
    "journal_invocation_records", "journal_timestamp_records",
)
ROLES = (
    ("SOURCE_CYCLE", "porota-iol-shadow-collector-rc6"),
    ("EOD_LOCAL", "porota-local-eod-history-rc6"),
    ("POSTCLOSE_SNAPSHOT", "porota-snapshot-postclose-rc6"),
    ("CANDLE_INTEGRITY", "porota-candle-integrity-rc6"),
)
CLASSIFICATIONS = frozenset({"NOT_DUE", "NO_SOURCE", "SCHEDULE_SILENT", "FAILED",
                             "RECENT_INVOCATIONS_OBSERVED", "NO_VERIFICADO"})
BUCKETS = frozenset({"NONE", "WITHIN_2M", "WITHIN_10M", "WITHIN_30M",
                     "OLDER", "UNKNOWN"})
SOURCE_STALE_CLASSES = frozenset({"STALE_OR_MISSING", "PUBLIC_STALE_OR_MISSING",
                                  "EVIDENCE_STALE_OR_MISSING", "BOTH_STALE_OR_MISSING"})
SOURCE_CLASSES = SOURCE_STALE_CLASSES | frozenset({"FRESH", "UNKNOWN", "NOT_APPLICABLE"})
LOAD_CLASSES = frozenset({"loaded", "masked", "not-found", "error", "UNKNOWN"})
ACTIVE_CLASSES = frozenset({"active", "inactive", "failed", "UNKNOWN"})
RESULT_CLASSES = frozenset({"success", "exit-code", "timeout", "signal", "core-dump",
                            "watchdog", "start-limit-hit", "protocol", "resources",
                            "dependency", "UNKNOWN"})


def nominal_slots(role: str, day: date) -> tuple[datetime, ...]:
    """Source-code nominal schedules, not attestation of effective host units."""
    if day.weekday() > 4:
        return ()
    minutes: list[int] = []
    if role == "SOURCE_CYCLE":
        minutes = list(range(10 * 60 + 30, 18 * 60))
    elif role == "EOD_LOCAL":
        minutes = list(range(17 * 60 + 15, 18 * 60, 5)) + list(range(18 * 60, 23 * 60, 5))
    elif role == "POSTCLOSE_SNAPSHOT":
        if day >= date(2026, 11, 2):
            minutes = [18 * 60 + 8] + [h * 60 + m for h in range(18, 22)
                                          for m in (20, 50)] + [22 * 60 + 20]
        else:
            minutes = [17 * 60 + 8]
    elif role == "CANDLE_INTEGRITY":
        minutes = ([10 * 60 + 35] + [h * 60 + m for h in range(11, 17)
                                      for m in (5, 35)] +
                   [17 * 60 + 5, 17 * 60 + 35, 18 * 60 + 5, 23 * 60 + 45])
    else:
        raise ValueError("UNKNOWN_ROLE")
    return tuple(datetime(day.year, day.month, day.day, hour=n // 60,
                          minute=n % 60, tzinfo=ART).astimezone(timezone.utc)
                 for n in minutes)


def _bounded_state_source(now: datetime) -> str:
    """Both producer stages fresh by their TTLs; never a PPI/Book assertion.

    Read only the fixed small state JSON, not databases, caches or instruments.
    The producer never writes ``last_iol_success_at``; requiring it produces
    false negatives even when both real stage clocks are current.
    """
    if now.tzinfo is None:
        return "UNKNOWN"
    fd = None
    directory = None
    try:
        # The fixed path is not accepted from inputs. Anchor every directory
        # component, like the existing A420 helper, without following aliases.
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory = os.open("/", flags | os.O_DIRECTORY)
        for part in SOURCE_STATE.parts[1:-1]:
            next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        fd = os.open(SOURCE_STATE.name, flags, dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 4096:
            return "UNKNOWN"
        raw = os.read(fd, 4097)
        after = os.fstat(fd)
        if len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_mtime_ns):
            return "UNKNOWN"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("schema") != SOURCE_STATE_SCHEMA:
            return "UNKNOWN"
        if data.get("session_date") != now.astimezone(ART).date().isoformat():
            return "STALE_OR_MISSING"
        stale_stages: set[str] = set()
        for field, ttl_seconds in SOURCE_STAGE_TTLS:
            stamp = data.get(field)
            if not isinstance(stamp, str):
                stale_stages.add(field)
                continue
            last = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if last.tzinfo is None:
                return "UNKNOWN"
            age = (now - last.astimezone(timezone.utc)).total_seconds()
            if age < 0:
                return "UNKNOWN"  # do not certify future clocks as fresh
            if age >= ttl_seconds:
                stale_stages.add(field)
        if not stale_stages:
            return "FRESH"
        if len(stale_stages) == len(SOURCE_STAGE_TTLS):
            return "BOTH_STALE_OR_MISSING"
        if "last_public_success_at" in stale_stages:
            return "PUBLIC_STALE_OR_MISSING"
        return "EVIDENCE_STALE_OR_MISSING"
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return "UNKNOWN"
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)


def _run(args: list[str], *, runner=subprocess.run, timeout: float) -> str | None:
    try:
        completed = runner(args, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.returncode or len(completed.stdout) > MAX_JOURNAL_BYTES:
            return None
        return completed.stdout
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _safe_enum(value: str | None, allowed: frozenset[str]) -> str:
    return value if value in allowed else "UNKNOWN"


def _int_field(pairs: dict[str, str], key: str) -> int | None:
    val = pairs.get(key, "")
    if not val.isascii() or not val.isdecimal() or len(val) > 18:
        return None
    return int(val)


def inspect_systemd(base: str, *, runner=subprocess.run) -> dict[str, object]:
    if base not in {x[1] for x in ROLES}:
        raise ValueError("UNKNOWN_UNIT")
    props = ["LoadState", "ActiveState", "Result", "ExecMainStatus",
             "ExecMainExitTimestampMonotonic", "LastTriggerUSecMonotonic", "NeedDaemonReload",
             "DropInPaths"]
    units = {}
    for suffix in ("timer", "service"):
        stdout = _run(["systemctl", "show", "--no-pager"] +
                      [f"--property={p}" for p in props] + [f"{base}.{suffix}"],
                      runner=runner, timeout=3)
        if stdout is None or len(stdout) > 4096:
            return {"query": "UNAVAILABLE", "timer_active": "UNKNOWN", "service_result": "UNKNOWN",
                    "last_trigger_us": None, "last_exit_us": None,
                    "schedule_parity_verified": False, "clock_compatible": False}
        pairs = dict(line.split("=", 1) for line in stdout.splitlines()
                     if "=" in line and len(line) <= 2048)
        # A local override or daemon-reload mismatch means source assumptions
        # may not match the installed timer. Fail closed.
        if pairs.get("NeedDaemonReload") != "no" or pairs.get("DropInPaths", "UNKNOWN") != "":
            return {"query": "UNAVAILABLE", "timer_active": "UNKNOWN", "service_result": "UNKNOWN",
                    "last_trigger_us": None, "last_exit_us": None,
                    "schedule_parity_verified": False, "clock_compatible": False}
        units[suffix] = pairs
    timer, service = units["timer"], units["service"]
    if timer.get("LoadState") != "loaded" or service.get("LoadState") != "loaded":
        return {"query": "UNAVAILABLE", "timer_active": "UNKNOWN", "service_result": "UNKNOWN",
                "last_trigger_us": None, "last_exit_us": None,
                "schedule_parity_verified": False, "clock_compatible": False}
    return {"query": "READ_ONLY", "timer_active": _safe_enum(timer.get("ActiveState"), ACTIVE_CLASSES),
            "service_result": _safe_enum(service.get("Result"), RESULT_CLASSES),
            "last_trigger_us": _int_field(timer, "LastTriggerUSecMonotonic"),
            "last_exit_us": _int_field(service, "ExecMainExitTimestampMonotonic"),
            # LoadState/NeedDaemonReload/DropInPaths do NOT prove the exact
            # installed OnCalendar or equivalence of CLOCK_BOOTTIME to the
            # systemd property clock. A future version must independently
            # attest both before it may claim actual schedule silence.
            "schedule_parity_verified": False, "clock_compatible": False}


def journal_invocations(unit: str, now: datetime, *, runner=subprocess.run,
                        with_metadata: bool = False):
    """Journal metadata census, bounded and MESSAGE-free.

    Counts describe only entries returned by the exact bounded query. They
    cannot prove complete systemd cadence or infer a missing provider Book.
    The legacy tuple-returning API remains for existing callers/tests.
    """
    if unit not in {base + ".service" for _, base in ROLES}:
        raise ValueError("UNKNOWN_UNIT")
    counts = dict.fromkeys(JOURNAL_COUNT_FIELDS, 0)
    def result(ticks):
        # Partial/malformed/truncated JSON is not a trustworthy census.
        safe_counts = counts if ticks is not None else dict.fromkeys(JOURNAL_COUNT_FIELDS, 0)
        return (ticks, safe_counts) if with_metadata else ticks
    start = now - timedelta(minutes=LOOKBACK_MINUTES)
    stdout = _run(["journalctl", "--no-pager", "--quiet", "--output=json",
                   "--output-fields=__REALTIME_TIMESTAMP,_SYSTEMD_UNIT,_SYSTEMD_INVOCATION_ID",
                   f"--since={start.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                   f"--until={now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                   f"--lines={MAX_JOURNAL_RECORDS}", "--unit", unit],
                  runner=runner, timeout=5)
    if stdout is None:
        return result(None)
    lines = stdout.splitlines()
    if len(lines) >= MAX_JOURNAL_RECORDS:
        return result(None)  # could be truncated, cannot prove complete coverage
    counts["journal_records"] = len(lines)
    seen: dict[str, datetime] = {}
    try:
        for line in lines:
            if len(line) > 4096:
                return result(None)
            event = json.loads(line)
            if not isinstance(event, dict):
                return result(None)
            if event.get("_SYSTEMD_UNIT") != unit:
                continue
            counts["journal_unit_records"] += 1
            ident = event.get("_SYSTEMD_INVOCATION_ID")
            stamp = event.get("__REALTIME_TIMESTAMP")
            if not isinstance(ident, str) or not INVOCATION.fullmatch(ident):
                continue  # manager messages have no invocation ID
            counts["journal_invocation_records"] += 1
            if not isinstance(stamp, str) or not stamp.isascii() or not stamp.isdecimal():
                continue
            counts["journal_timestamp_records"] += 1
            tick = datetime.fromtimestamp(int(stamp) / 1_000_000, timezone.utc)
            if start <= tick <= now and (ident not in seen or tick < seen[ident]):
                seen[ident] = tick
    except (ValueError, OverflowError, TypeError):
        return result(None)
    return result(tuple(sorted(seen.values())))


def journal_metadata_state(invocations, counts, observed_distinct_slots: int,
                           *, matching_evaluated: bool = True) -> str:
    """Explains the first failed evidence gate; never attributes cause to systemd."""
    if invocations is None:
        return "UNAVAILABLE"
    if counts is None:
        return "NOT_COLLECTED"
    total, unit, ident, stamp = (counts[field] for field in JOURNAL_COUNT_FIELDS)
    if not total:
        return "NO_JOURNAL_ROWS"
    if not unit:
        return "UNIT_FIELD_UNAVAILABLE"
    if not ident:
        return "INVOCATION_ID_UNAVAILABLE"
    if not stamp:
        return "REALTIME_TIMESTAMP_UNAVAILABLE"
    if not matching_evaluated:
        return "MATCHING_NOT_EVALUATED"
    return "MATCHED_NOMINAL_SLOTS" if observed_distinct_slots else "NO_NOMINAL_SLOT_MATCH"


def classify(role: str, now: datetime, invocations: tuple[datetime, ...] | None,
             host: dict[str, object], *, boot_us: int | None,
             source: str = "NOT_APPLICABLE",
             journal_counts: dict[str, int] | None = None) -> dict[str, object]:
    if role not in {x[0] for x in ROLES} or now.tzinfo is None:
        raise ValueError("BAD_CLASSIFICATION_INPUT")
    within = [s for s in nominal_slots(role, now.astimezone(ART).date())
              if now - timedelta(minutes=LOOKBACK_MINUTES) <= s <= now]
    mature = [s for s in within if s <= now - timedelta(seconds=MATURE_GRACE_SECONDS)]
    result: dict[str, object] = {"role": role, "classification": "NO_VERIFICADO",
                                  "nominal_mature_slots": len(mature),
                                  "observed_distinct_slots": 0,
                                  "last_two_mature_distinct": False,
                                  "journal_available": invocations is not None,
                                  "schedule_parity_verified": False,
                                  "clock_compatible": False,
                                  "source": source if source in SOURCE_CLASSES else "UNKNOWN",
                                  "timer": "UNKNOWN", "service": "UNKNOWN",
                                  "full_day_cadence_certified": False,
                                  "journal_metadata_state": journal_metadata_state(
                                      invocations, journal_counts, 0,
                                      matching_evaluated=False),
                                  **(journal_counts if journal_counts is not None else
                                     dict.fromkeys(JOURNAL_COUNT_FIELDS, 0))}
    if not mature:
        result["classification"] = "NOT_DUE"
        return result
    if host.get("query") != "READ_ONLY" or boot_us is None or boot_us <= 0:
        return result
    result["timer"] = host.get("timer_active") if host.get("timer_active") in ACTIVE_CLASSES else "UNKNOWN"
    result["service"] = host.get("service_result") if host.get("service_result") in RESULT_CLASSES else "UNKNOWN"
    result["schedule_parity_verified"] = host.get("schedule_parity_verified") is True
    result["clock_compatible"] = host.get("clock_compatible") is True
    last_trigger = host.get("last_trigger_us")
    last_exit = host.get("last_exit_us")
    if not isinstance(last_trigger, int) or last_trigger <= 0 or last_trigger > boot_us:
        return result
    # A fresh boot with less uptime than this evidence window cannot prove
    # missed slots before boot. Only the last two mature slots are assessed.
    age_s = (boot_us - last_trigger) / 1_000_000
    fresh_limit = min(600, max(180, (mature[-1] - mature[-2]).total_seconds() + 120)) if len(mature) >= 2 else 180
    if (result["timer"] == "active" and len(mature) >= 2 and
            invocations is not None and result["schedule_parity_verified"] and
            result["clock_compatible"] and age_s > 2 * fresh_limit and
            boot_us >= LOOKBACK_MINUTES * 60 * 1_000_000):
        # A contradictory recent service invocation warrants investigation;
        # it cannot become a definitive schedule-silence conclusion.
        if invocations is not None and any(t >= mature[-2] for t in invocations):
            return result
        result["classification"] = "SCHEDULE_SILENT"
        return result
    if invocations is None:
        return result
    unused = list(invocations)
    matched: list[datetime] = []
    for slot in mature:
        # Non-overlapping nominal windows, one unique invocation per slot.
        max_delay = min(90, (mature[1] - mature[0]).total_seconds()) if len(mature) >= 2 else 90
        hits = [t for t in unused if slot <= t < slot + timedelta(seconds=max_delay)]
        if hits:
            t = min(hits)
            matched.append(slot)
            unused.remove(t)
    result["observed_distinct_slots"] = len(matched)
    result["journal_metadata_state"] = journal_metadata_state(
        invocations, journal_counts, len(matched))
    result["last_two_mature_distinct"] = len(mature) >= 2 and all(x in matched for x in mature[-2:])
    if not result["last_two_mature_distinct"]:
        return result
    # A historic failure must not be assigned to recent triggers: require the
    # last service exit to follow the timer trigger in the same boot.
    if (result["service"] not in {"success", "UNKNOWN"} and isinstance(last_exit, int)
            and last_trigger <= last_exit <= boot_us):
        result["classification"] = "FAILED"
    elif (role == "SOURCE_CYCLE" and source in SOURCE_STALE_CLASSES and
          now.astimezone(ART).time() < wall_time(17, 0)):
        # After 17:00 COM19031 narrows observation to exact T+1 contracts.
        # A successful NOT_DUE_LATE_NO_VERIFIED_T1 tick is legitimate; the
        # generic state JSON has no contract-level reason to distinguish it
        # from a provider/source failure. Never label it NO_SOURCE here.
        result["classification"] = "NO_SOURCE"
    elif result["service"] == "success" and (role != "SOURCE_CYCLE" or source == "FRESH"):
        result["classification"] = "RECENT_INVOCATIONS_OBSERVED"
    return result


def default_receipt() -> dict[str, object]:
    return {"schema": SCHEMA, "read_only": True, "host_metadata_only": True,
            "database_opened": False, "provider_calls": 0, "order_routes_called": False,
            "ppi_watch_touched": False, "real_orders_sent_by_action": 0,
            "full_day_cadence_certified": False, "probe_status": "NO_VERIFICADO", "roles": []}


def host_probe(*, now: datetime | None = None, runner=subprocess.run,
               boot_us: int | None = None) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("AWARE_UTC_REQUIRED")
    receipt = default_receipt()
    if boot_us is None:
        try:
            boot_us = int(time.clock_gettime(time.CLOCK_BOOTTIME) * 1_000_000)
        except (AttributeError, OSError, ValueError):
            boot_us = None
    source = _bounded_state_source(now)
    for role, base in ROLES:
        host = inspect_systemd(base, runner=runner)
        journal, counts = journal_invocations(base + ".service", now, runner=runner,
                                             with_metadata=True)
        receipt["roles"].append(classify(role, now, journal, host, boot_us=boot_us,
                                         source=source if role == "SOURCE_CYCLE" else "NOT_APPLICABLE",
                                         journal_counts=counts))
    receipt["probe_status"] = "METADATA_ONLY" if all(
        item["timer"] != "UNKNOWN" for item in receipt["roles"]
        if item["classification"] != "NOT_DUE") else "NO_VERIFICADO"
    return receipt


def sanitize_receipt(raw: bytes) -> dict[str, object]:
    """Untrusted SSH output cannot introduce free text or leak host details."""
    fallback = default_receipt()
    if len(raw) > 16 * 1024:
        return fallback
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or set(data) != set(fallback):
            return fallback
        for key in set(fallback) - {"roles", "probe_status"}:
            if type(data[key]) is not type(fallback[key]) or data[key] != fallback[key]:
                return fallback
        if data["probe_status"] not in {"METADATA_ONLY", "NO_VERIFICADO"}:
            return fallback
        items = data["roles"]
        if not isinstance(items, list) or len(items) != len(ROLES):
            return fallback
        fields = {"role", "classification", "nominal_mature_slots", "observed_distinct_slots",
                  "last_two_mature_distinct", "source", "timer", "service",
                  "journal_available", "schedule_parity_verified", "clock_compatible",
                  "full_day_cadence_certified", "journal_metadata_state",
                  *JOURNAL_COUNT_FIELDS}
        for (role, _), item in zip(ROLES, items):
            if (not isinstance(item, dict) or set(item) != fields or item["role"] != role or
                item["classification"] not in CLASSIFICATIONS or item["source"] not in SOURCE_CLASSES or
                item["timer"] not in ACTIVE_CLASSES or item["service"] not in RESULT_CLASSES or
                type(item["nominal_mature_slots"]) is not int or
                type(item["observed_distinct_slots"]) is not int or
                not 0 <= item["observed_distinct_slots"] <= item["nominal_mature_slots"] <= LOOKBACK_MINUTES + 2 or
                type(item["last_two_mature_distinct"]) is not bool or
                type(item["journal_available"]) is not bool or
                item["journal_metadata_state"] not in JOURNAL_METADATA_STATES or
                type(item["schedule_parity_verified"]) is not bool or
                type(item["clock_compatible"]) is not bool or
                item["full_day_cadence_certified"] is not False):
                return fallback
            counts = tuple(item[field] for field in JOURNAL_COUNT_FIELDS)
            if (any(type(count) is not int for count in counts) or
                    not 0 <= counts[3] <= counts[2] <= counts[1] <= counts[0] < MAX_JOURNAL_RECORDS):
                return fallback
            expected_meta = journal_metadata_state(
                () if item["journal_available"] else None,
                dict(zip(JOURNAL_COUNT_FIELDS, counts))
                if item["journal_metadata_state"] != "NOT_COLLECTED" else None,
                item["observed_distinct_slots"],
                matching_evaluated=item["journal_metadata_state"] != "MATCHING_NOT_EVALUATED")
            if (item["journal_metadata_state"] != expected_meta or
                    (not item["journal_available"] and any(counts)) or
                    (item["journal_metadata_state"] == "MATCHING_NOT_EVALUATED"
                     and (item["classification"] != "NO_VERIFICADO"
                          or item["observed_distinct_slots"] != 0)) or
                    (item["journal_metadata_state"] != "NOT_COLLECTED" and
                     item["observed_distinct_slots"] > counts[3])):
                return fallback
            if item["classification"] == "RECENT_INVOCATIONS_OBSERVED" and not item["last_two_mature_distinct"]:
                return fallback
            if item["classification"] == "NOT_DUE" and item["nominal_mature_slots"] != 0:
                return fallback
            if item["last_two_mature_distinct"] and item["observed_distinct_slots"] < 2:
                return fallback
            if item["classification"] in {"NO_SOURCE", "FAILED"} and not item["last_two_mature_distinct"]:
                return fallback
            if item["classification"] == "NO_SOURCE" and not (
                    role == "SOURCE_CYCLE" and item["source"] in SOURCE_STALE_CLASSES):
                return fallback
            if item["classification"] == "SCHEDULE_SILENT" and not (
                    item["nominal_mature_slots"] >= 2 and item["timer"] == "active" and
                    item["journal_available"] and item["schedule_parity_verified"] and
                    item["clock_compatible"]):
                return fallback
            if item["classification"] == "RECENT_INVOCATIONS_OBSERVED" and not (
                    item["service"] == "success" and
                    (role != "SOURCE_CYCLE" or item["source"] == "FRESH")):
                return fallback
        return data
    except (ValueError, TypeError, UnicodeError):
        return fallback


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["host-probe"]:
        print(json.dumps(host_probe(), sort_keys=True, separators=(",", ":")))
        return 0
    if args == ["sanitize"]:
        path = Path("evidence/.a504-raw.json")
        try:
            with path.open("rb") as handle:
                raw = handle.read(16 * 1024 + 1)
        except OSError:
            raw = b""
        result = sanitize_receipt(raw)
        Path("evidence/a504-cadence.json").write_text(
            json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        print("A504_SYSTEMD_CADENCE=" + str(result["probe_status"]))
        return 0
    print("A504_READ_ONLY_USAGE_ERROR", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
