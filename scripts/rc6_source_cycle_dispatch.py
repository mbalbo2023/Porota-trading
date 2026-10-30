#!/usr/bin/env python3
"""Bounded host scheduler for the existing RC6 read-only source cycle.

The systemd timer fires each minute during the currently audited IOL window.
IOL and public BYMA/CNV/A3 capture retain their one-minute cadence because
the live SHADOW radar consumes the public snapshot directly. Full-universe
evidence has a five-minute TTL. These deadlines are
durably recorded so a slow IOL run cannot accidentally skip a boundary.

No broker, order route or provider client is imported into this dispatcher.
Each child is an explicit allowlisted observational script, admitted only
after a read-only PAPER/zero-order check and one exclusive source-cycle lock.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import date, datetime, time as clock_time, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

import ak_byma_calendar as calendar


ROOT = Path(__file__).resolve().parents[1]
ART = ZoneInfo("America/Argentina/Buenos_Aires")
OPEN = clock_time(10, 30)
CLOSE = clock_time(17, 0)  # Current collector baseline; 02-Nov family extension is separate.
LATE_CLOSE = clock_time(18, 0)
MORNING_OPEN = clock_time(8, 30)
MORNING_END = clock_time(9, 0)
COM19024_EFFECTIVE = date(2026, 11, 2)
COM19024_REVIEWED_THROUGH = date(2026, 12, 31)
# This is a live SHADOW input, not an optional background scraper. A longer
# TTL would silently change source-observation cadence and SHADOW checkpoints.
PUBLIC_TTL_SECONDS = 60
EVIDENCE_TTL_SECONDS = 5 * 60
STATE_SCHEMA = "rc6.source-cycle-dispatch.v1"
DEFAULT_MARKET_ROOT = Path(os.getenv("POROTA_IOL_SHADOW_ROOT", "/opt/porota-trading/data/market"))
DEFAULT_DB = Path(os.getenv("PAPER_V17_DB_PATH", "/opt/porota-trading/data/paper_v17/observer_v17.db"))
DEFAULT_LOCK = Path("/var/lock/porota-rc6-source-cycle.lock")
STAGES = (
    ("iol", "scripts/rc6_iol_shadow_collect.py", 110,
     "iol_shadow_latest.json"),
    ("public", "scripts/rc6_public_source_capture.py", 50,
     "rc6_public_sources_latest.json"),
    ("evidence", "scripts/rc6_full_universe_evidence.py", 25,
     "rc6_instrument_evidence_latest.json"),
)


class SourceCycleBlocked(ValueError):
    """An unproven clock, state, identity or safety condition fails closed."""


def phase_status(instant: datetime, phase: str) -> tuple[bool, str]:
    """Check an audited Argentine trading day before scheduling external work.

    The OPEN phase is intentionally the current *collector* window, not an
    assertion that every family closes at 17:00 after the COM19024 transition.
    """
    if instant.tzinfo is None:
        return False, "CLOCK_MISSING_TIMEZONE"
    local = instant.astimezone(ART)
    if local.year not in calendar.ANIOS_AUDITADOS:
        return False, "CALENDAR_YEAR_UNVERIFIED"
    try:
        if not calendar.es_dia_habil_operativo(local.date()):
            return False, "NON_TRADING_DAY"
    except (KeyError, TypeError, ValueError, OSError):
        return False, "CALENDAR_UNVERIFIED"
    if phase == "business_day":
        return True, "AUDITED_BUSINESS_DAY"
    if phase == "morning":
        return (MORNING_OPEN <= local.time() < MORNING_END,
                "MORNING_WINDOW" if MORNING_OPEN <= local.time() < MORNING_END
                else "OUTSIDE_MORNING_WINDOW")
    if phase == "db_postclose":
        allowed = clock_time(23, 20) <= local.time() < clock_time(23, 45)
        return allowed, "DB_POSTCLOSE_WINDOW" if allowed else "OUTSIDE_DB_POSTCLOSE"
    if phase == "postclose_snapshot":
        # COM18782 closes regular PPT at 17 until 01-Nov. COM19024 moves
        # regular equities/options to 18 starting 02-Nov while caucions remain
        # 17. The single global snapshot belongs after the LAST verified close.
        hour = 18 if local.date() >= COM19024_EFFECTIVE else 17
        allowed = clock_time(hour, 8) <= local.time() < clock_time(hour, 30)
        return allowed, "VERIFIED_PPT_CLOSE" if allowed else "PPT_STILL_OPEN_OR_TOO_LATE"
    if phase == "open":
        allowed = OPEN <= local.time() < CLOSE
        return allowed, "COLLECTOR_BASELINE_WINDOW" if allowed else "OUTSIDE_COLLECTOR_WINDOW"
    if phase == "late_source":
        # This admits a bounded *attempt*, not a market-wide quote collection.
        # The IOL child selects only PPI_FIELD/BYMA/A-24HS exact identities;
        # futures, cash-immediate and cauciones cannot inherit an 18:00 close.
        allowed = (COM19024_EFFECTIVE <= local.date() <= COM19024_REVIEWED_THROUGH
                   and CLOSE <= local.time() < LATE_CLOSE)
        return allowed, "COM19024_EXACT_LATE_SOURCE" if allowed else "LATE_SOURCE_NOT_DUE"
    raise SourceCycleBlocked("UNKNOWN_PHASE")


def _paper_zero(db: Path) -> None:
    if not db.is_absolute() or not db.is_file() or db.is_symlink():
        raise SourceCycleBlocked("PAPER_DATABASE_IDENTITY_UNVERIFIED")
    try:
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=.25)) as c:
            c.execute("PRAGMA query_only=ON")
            row = c.execute(
                "SELECT mode,real_orders_sent FROM observer_state WHERE id=1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise SourceCycleBlocked("PAPER_DATABASE_UNVERIFIED") from exc
    if row != ("PRODUCTION_PAPER", 0):
        raise SourceCycleBlocked("PAPER_ZERO_REQUIRED")


def _state_read(path: Path, now: datetime) -> dict:
    try:
        meta = path.lstat()
    except FileNotFoundError:
        return {"schema": STATE_SCHEMA, "session_date": None,
                "last_public_success_at": None, "last_evidence_success_at": None}
    if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size > 4096:
        raise SourceCycleBlocked("SOURCE_CYCLE_STATE_UNSAFE")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SourceCycleBlocked("SOURCE_CYCLE_STATE_INVALID") from exc
    if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
        raise SourceCycleBlocked("SOURCE_CYCLE_STATE_SCHEMA_INVALID")
    if payload.get("session_date") is not None and not isinstance(payload.get("session_date"), str):
        raise SourceCycleBlocked("SOURCE_CYCLE_SESSION_DATE_INVALID")
    for key in ("last_public_success_at", "last_evidence_success_at"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise SourceCycleBlocked("SOURCE_CYCLE_CLOCK_INVALID") from exc
        if parsed.tzinfo is None or parsed.astimezone(timezone.utc) > now.astimezone(timezone.utc):
            raise SourceCycleBlocked("SOURCE_CYCLE_CLOCK_INVALID")
    return payload


def _due(state: dict, field: str, now: datetime, ttl: int) -> bool:
    if state.get("session_date") != now.astimezone(ART).date().isoformat():
        return True
    value = state.get(field)
    if value is None:
        return True
    last = datetime.fromisoformat(value)
    return (now.astimezone(timezone.utc) - last.astimezone(timezone.utc)).total_seconds() >= ttl


def _state_write(path: Path, data: dict) -> None:
    if path.is_symlink():
        raise SourceCycleBlocked("SOURCE_CYCLE_STATE_ALIAS")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".source-cycle-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o644)
            json.dump(data, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _exclusive_lock(path: Path):
    if not path.is_absolute():
        raise SourceCycleBlocked("SOURCE_CYCLE_LOCK_PATH_UNSAFE")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise SourceCycleBlocked("SOURCE_CYCLE_LOCK_UNSAFE")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceCycleBlocked("SOURCE_CYCLE_LOCK_BUSY") from exc
        yield
    finally:
        os.close(fd)


def _chmod_owned(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SourceCycleBlocked("SOURCE_CYCLE_OUTPUT_UNSAFE")
    path.chmod(0o644)


def _published_file_identity(path: Path) -> tuple[int, ...] | None:
    """Identify an output without following aliases or inspecting provider data.

    A zero exit code (or a collector's success marker) cannot prove that the
    current cycle published anything: an old file may still be present. The
    actual producers atomically replace their snapshots, so inode/metadata
    identity is sufficient even when two successive snapshots have identical
    content. This is publication evidence, not quote-freshness certification.
    """
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise SourceCycleBlocked("SOURCE_CYCLE_OUTPUT_UNSAFE")
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def _child_environment(market_root: Path, db: Path) -> dict[str, str]:
    """One canonical output/DB identity for all three already-existing jobs.

    An operator override pointing at another root must not silently create
    conflicting .tmp writers or make the published evidence follow another DB.
    """
    canonical = {
        "POROTA_IOL_SHADOW_ROOT": market_root,
        "POROTA_PUBLIC_SOURCE_CAPTURE_PATH": market_root / "rc6_public_sources_latest.json",
        "POROTA_EVIDENCE_ROOT": market_root,
        "POROTA_FULL_EVIDENCE_PATH": market_root / "rc6_instrument_evidence_latest.json",
        "POROTA_IOL_OPERATIONAL_DB": db,
        "POROTA_OBSERVER_DB": db,
    }
    for key, target in canonical.items():
        configured = os.getenv(key)
        if configured is not None and Path(configured) != target:
            raise SourceCycleBlocked("SOURCE_CYCLE_PATH_OVERRIDE_DIVERGED:" + key)
    return {**os.environ, **{key: str(value) for key, value in canonical.items()}}


def run_cycle(*, now: datetime | None = None, db: Path = DEFAULT_DB,
              market_root: Path = DEFAULT_MARKET_ROOT, lock: Path = DEFAULT_LOCK,
              runner=subprocess.run) -> dict:
    now = now or datetime.now(timezone.utc)
    regular, reason = phase_status(now, "open")
    late, _ = phase_status(now, "late_source")
    if not regular and not late:
        return {"status": "NOT_DUE", "reason": reason, "stages": {}}
    if os.getenv("POROTA_IOL_SHADOW_MODE", "OBSERVE_ONLY").strip() != "OBSERVE_ONLY":
        raise SourceCycleBlocked("OBSERVE_ONLY_REQUIRED")
    if not market_root.is_absolute() or market_root.is_symlink():
        raise SourceCycleBlocked("SOURCE_CYCLE_OUTPUT_ROOT_UNSAFE")
    _paper_zero(db)
    child_env = _child_environment(market_root, db)
    state_path = market_root / "rc6_source_cycle_dispatch_state.json"
    with _exclusive_lock(lock):
        state = _state_read(state_path, now)
        public_due = _due(state, "last_public_success_at", now, PUBLIC_TTL_SECONDS)
        # A public refresh does not imply that the heavier full-universe
        # projection must run at every SHADOW source tick. Its own bounded TTL
        # is independent, and consumers still recompute evidence freshness.
        evidence_due = _due(state, "last_evidence_success_at", now, EVIDENCE_TTL_SECONDS)
        due = {"iol": True, "public": public_due, "evidence": evidence_due}
        results = {}
        changed = False
        # After 17:00 only the exact T+1/regular-options BYMA IOL subset may
        # be observed. Public-source sweeping and global projections keep the
        # established baseline and are not silently extended by this timer.
        stages = STAGES[:1] if late else STAGES
        for name, relative, timeout, output in stages:
            if not due[name]:
                results[name] = "NOT_DUE_TTL"
                continue
            output_path = market_root / output
            before_publication = _published_file_identity(output_path)
            try:
                result = runner([sys.executable, str(ROOT / relative)], cwd=str(ROOT),
                                env=child_env, capture_output=True, text=True,
                                timeout=timeout, check=False)
            except (OSError, subprocess.TimeoutExpired):
                results[name] = "EXECUTION_UNVERIFIED"
                continue
            if result.returncode:
                results[name] = "FAILED"
                continue
            # The legacy collector returns zero even when it declines a cycle.
            # A successful Python process is not proof of an IOL observation.
            if name == "iol" and not any(
                    line.startswith("IOL_SHADOW_COLLECTION=COMPLETE ")
                    for line in (result.stdout or "").splitlines()):
                if late and any(
                        line.startswith("IOL_SHADOW_COLLECTION=NOT_DUE_LATE_NO_VERIFIED_T1 ")
                        for line in (result.stdout or "").splitlines()):
                    results[name] = "NOT_DUE_LATE_EXACT_CONTRACT"
                    continue
                results[name] = "COLLECTOR_NOT_COMPLETED"
                continue
            try:
                after_publication = _published_file_identity(output_path)
                if after_publication is None or after_publication == before_publication:
                    # Never advance public/evidence TTLs on a retained output.
                    # This also protects IOL when an old output coexists with
                    # a misleading success marker from a future code change.
                    results[name] = "OUTPUT_NOT_REPUBLISHED"
                    continue
                _chmod_owned(output_path)
            except (OSError, ValueError):
                results[name] = "OUTPUT_UNVERIFIED"
                continue
            results[name] = "OK"
            if name in {"public", "evidence"}:
                state["session_date"] = now.astimezone(ART).date().isoformat()
                state[f"last_{name}_success_at"] = now.astimezone(timezone.utc).isoformat()
                changed = True
        if changed:
            _state_write(state_path, state)
    status = ("NOT_DUE" if late and set(results.values()) == {"NOT_DUE_LATE_EXACT_CONTRACT"}
              else "COMPLETE" if all(value in {"OK", "NOT_DUE_TTL"}
                                     for value in results.values()) else "DEGRADED")
    return {"status": status,
            "reason": "COM19024_EXACT_LATE_T1_OBSERVE_ONLY" if late else "PAPER_OBSERVE_ONLY",
            "stages": results, "real_orders_sent": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-phase", choices=("morning", "open", "late_source", "business_day", "db_postclose", "postclose_snapshot"))
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.check_phase:
        ok, _ = phase_status(now, args.check_phase)
        return 0 if ok else 1
    try:
        result = run_cycle(now=now)
    except (OSError, ValueError) as exc:
        # Stable class/reason only: no OAuth token, provider data or DB paths.
        result = {"status": "BLOCKED", "reason": str(exc)[:80], "stages": {}}
    print("RC6_SOURCE_CYCLE_DISPATCH=" + json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"NOT_DUE", "COMPLETE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
