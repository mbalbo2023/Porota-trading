#!/usr/bin/env python3
"""A669 one-day control-plane gate for Thursday PPI Current/Book evidence.

Only validates a pinned, existing A630 OFFHOST-tested capture engine and the
2026-11-05 ART time slot.  It never imports the provider SDK, accesses secrets,
opens the production host, or acquires a PPI lease.  All provider permissions
remain in the independently reviewed existing capture engine at the expected
Git blob, with fail-closed shared-budget/EXIT checks.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
from zoneinfo import ZoneInfo


WORKSTREAM = "A669"
TARGET_DAY = date(2026, 11, 5)
ZONE = ZoneInfo("America/Argentina/Buenos_Aires")
REPOSITORY = "mbalbo2023/Porota-trading"
CONTROL_REF = "refs/heads/main"
ENGINE_PATH = Path("scripts/rc6_a630_host_ppi_current_book.py")
ENGINE_GIT_BLOB = "6aeefcd76f34fd23c5fbe118dc440c14357c623c"
SCHEDULE_TO_SLOT = {
    "45 13 * * 4": "1045",
    "20 14 * * 4": "1120",
    "30 17 * * 4": "1430",
    "30 19 * * 4": "1630",
}
SLOT_MINUTES = {"1045": 10 * 60 + 45, "1120": 11 * 60 + 20,
                "1430": 14 * 60 + 30, "1630": 16 * 60 + 30}
HEX40 = re.compile(r"[0-9a-f]{40}\Z")


def git_blob_sha(contents: bytes) -> str:
    """Git's SHA-1 object identifier, not an ordinary file SHA-1 digest."""
    return hashlib.sha1(b"blob " + str(len(contents)).encode("ascii")
                        + b"\0" + contents).hexdigest()


def pinned_engine_ok(path: Path, expected_blob: str = ENGINE_GIT_BLOB) -> bool:
    """No follow-symlink and no changed/unbounded helper before potential SSH."""
    try:
        if path.is_symlink():
            return False
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode) or not 0 < file_stat.st_size <= 1_000_000:
            return False
        contents = path.read_bytes()
        return len(contents) == file_stat.st_size and git_blob_sha(contents) == expected_blob
    except (OSError, ValueError):
        return False


def eligible_slot(*, now_utc: datetime, event: str, schedule: str,
                  manual_slot: str, repository: str, ref: str,
                  checkout_sha: str, source_sha: str,
                  engine_path: Path, expected_engine_blob: str = ENGINE_GIT_BLOB):
    """Return (slot, reason); a nonempty slot is the ONLY permission to proceed.

    The caller must run this *before* reaching SSH or any provider/Watch route.
    Passing this day gate is never a PPI GET authorization: that additionally
    requires the existing shared RuntimePPIBudget's live signed leases.
    """
    if repository != REPOSITORY or ref != CONTROL_REF:
        return "", "NONCANONICAL_REPOSITORY_OR_REF"
    if (not isinstance(checkout_sha, str) or not isinstance(source_sha, str)
            or HEX40.fullmatch(checkout_sha) is None or checkout_sha != source_sha):
        return "", "EXACT_CHECKOUT_SHA_REQUIRED"
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
        return "", "AWARE_UTC_CLOCK_REQUIRED"
    try:
        observed = now_utc.astimezone(timezone.utc)
        if observed.utcoffset().total_seconds() != 0:
            return "", "AWARE_UTC_CLOCK_REQUIRED"
        art = observed.astimezone(ZONE)
    except (TypeError, ValueError, OverflowError):
        return "", "AWARE_UTC_CLOCK_REQUIRED"
    if art.date() != TARGET_DAY or art.weekday() != 3:
        return "", "ART_THURSDAY_05_NOV_REQUIRED"
    if event == "schedule":
        slot = SCHEDULE_TO_SLOT.get(schedule, "")
        lag_minutes = 30
    elif event == "workflow_dispatch":
        slot = manual_slot if manual_slot in SLOT_MINUTES else ""
        lag_minutes = 60
    else:
        return "", "NON_SAMPLING_EVENT"
    if not slot:
        return "", "UNKNOWN_CRON_OR_MANUAL_SLOT"
    observed_minute = art.hour * 60 + art.minute
    if not SLOT_MINUTES[slot] <= observed_minute <= SLOT_MINUTES[slot] + lag_minutes:
        return "", "OUTSIDE_ART_SLOT_WINDOW"
    if not pinned_engine_ok(engine_path, expected_engine_blob):
        return "", "AUDITED_ENGINE_BLOB_MISMATCH"
    return slot, "PASS_DAY_SLOT_AND_ENGINE_ONLY"


def _append_verified_env(path: Path, slot: str) -> bool:
    """Append only two constant-shape fields after a successful immutable gate."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            payload = (f"RC6_A669_SLOT={slot}\n"
                       f"RC6_A669_ENGINE_BLOB={ENGINE_GIT_BLOB}\n").encode("ascii")
            return os.write(fd, payload) == len(payload)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False


def main() -> int:
    """CI-only entry point: no user input or host/provider network operations."""
    try:
        current_sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            text=True, timeout=3, stderr=subprocess.DEVNULL).strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        current_sha = ""
    slot, reason = eligible_slot(
        now_utc=datetime.now(timezone.utc),
        event=os.environ.get("GITHUB_EVENT_NAME", ""),
        schedule=os.environ.get("RC6_A669_SCHEDULE", ""),
        manual_slot=os.environ.get("RC6_A669_MANUAL_SLOT", ""),
        repository=os.environ.get("GITHUB_REPOSITORY", ""),
        ref=os.environ.get("GITHUB_REF", ""),
        checkout_sha=current_sha,
        source_sha=os.environ.get("GITHUB_SHA", ""),
        engine_path=ENGINE_PATH)
    if not slot or not _append_verified_env(Path(os.environ.get("GITHUB_ENV", "")), slot):
        print(f"A669_GUARD=HOLD|REASON={reason if not slot else 'ENV_WRITE_DENIED'}|PPI_GETS_BY_GUARD=0")
        return 2
    print(f"A669_GUARD=PASS|SLOT={slot}|ART_DATE={TARGET_DAY}|PPI_GETS_BY_GUARD=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
