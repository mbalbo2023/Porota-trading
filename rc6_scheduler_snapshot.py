"""Clock and freshness contract for the existing RC6 systemd export."""
from datetime import datetime, timedelta, timezone
import re

INTERVAL_SECONDS = 5 * 60
ACCURACY_SECONDS = 15
CLOSED_REUSE_SECONDS = 9 * 60
EXPORT_UNIT = "porota-scheduler-export-rc6.timer"
_UNITS = {"w": 604800, "d": 86400, "h": 3600, "min": 60, "s": 1,
          "ms": .001, "us": .000001, "µs": .000001}
_TERM = re.compile(r"(\d+(?:\.\d+)?)(min|ms|us|µs|[wdhs])")


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def systemd_clock(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def execution_state(properties, trigger):
    """Interpret systemd's execution evidence independently of timer state."""
    load = properties.get("LoadState")
    if load in {"not-found", "masked"}:
        return "UNIT_UNAVAILABLE"
    if load != "loaded":
        return "UNKNOWN"
    code = properties.get("ExecMainCode")
    if code == "0" and properties.get("Result") == "exec-condition":
        return "SKIPPED_BY_CONDITION"
    if code in {"1", "2", "3"}:
        return "COMPLETED"
    started = systemd_clock(properties.get("ExecMainStartTimestamp"))
    if started and properties.get("SubState") in {"start", "running"}:
        return "RUNNING"
    finished = systemd_clock(properties.get("ExecMainExitTimestamp"))
    if (code == "0" and not started and not finished and not systemd_clock(trigger)
            and properties.get("ActiveState") == "inactive"):
        return "NO_EXECUTION_OBSERVED"
    return "UNKNOWN"


def scheduled_export_published(row, captured):
    """A current export proves publication during the scheduled invocation."""
    if (row.get("unit") != EXPORT_UNIT or row.get("service") != EXPORT_UNIT[:-6] + ".service"
            or row.get("execution_state") != "RUNNING"
            or row.get("service_sub_state") != "start"
            or row.get("last_result") != "unknown"):
        return False
    trigger = systemd_clock(row.get("last_trigger"))
    started = systemd_clock(row.get("service_started_at"))
    return bool(captured and trigger and started
                and 0 <= (started - trigger).total_seconds() <= ACCURACY_SECONDS
                and 0 <= (captured - started).total_seconds() <= INTERVAL_SECONDS)


def snapshot_phase(now):
    if now.tzinfo is None:
        return "UNKNOWN"
    try:
        import ak_byma_calendar as calendar
        from co_market_sessions_hf6 import TZ, byma_paper_spot_phase
    except ImportError:
        return "UNKNOWN"
    day = now.astimezone(TZ).date()
    if day.year not in calendar.ANIOS_AUDITADOS or not 0 <= (day - calendar.CALENDARIO_ACTUALIZADO_EL).days <= 60:
        return "UNKNOWN"
    return byma_paper_spot_phase(now)


def freshness_budget(captured, now, count):
    # A closed-market tick can intentionally reuse a previous snapshot. Once
    # its reuse window ends, the following scheduled tick must refresh it.
    reuse = (CLOSED_REUSE_SECONDS if captured and count >= 20
             and snapshot_phase(now) == snapshot_phase(captured) == "CLOSED" else 0)
    return INTERVAL_SECONDS + ACCURACY_SECONDS + reuse


def monotonic_target(value, *, wall, monotonic):
    """Convert the systemd uptime deadline at observation time, not on read."""
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    parts = [_TERM.fullmatch(part) for part in value.split()]
    if not parts or not all(parts):
        return None
    tick = sum(float(part[1]) * _UNITS[part[2]] for part in parts)
    try:
        if tick >= monotonic:
            return wall + timedelta(seconds=tick - monotonic)
    except OverflowError:
        pass
    return None
