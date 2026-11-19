#!/usr/bin/env python3
"""A670 manual-only control-plane gate for audited BYMA PPI Current/Book evidence.

Validates the immutable existing A630 OFFHOST-tested capture engine, exact
weekday ART slot, and product-pinned official BYMA operational-day calendar.
The source code has NO fixed expiry day; unknown years, stale
audits, holidays and closed sessions are evidence HOLD. This script never
imports a broker SDK, accesses secrets, opens production host or acquires a
PPI lease. Provider permissions remain exclusively in the existing audited
capture engine, subject to its existing runtime shared-budget/EXIT/lease
checks, or a separate explicitly manual local two-GET diagnostic at zero
observed PAPER positions. Neither is an official provider-quota attestation.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from zoneinfo import ZoneInfo


WORKSTREAM = "A670"
ZONE = ZoneInfo("America/Argentina/Buenos_Aires")
REPOSITORY = "mbalbo2023/Porota-trading"
CONTROL_REF = "refs/heads/main"
ENGINE_PATH = Path("scripts/rc6_a630_host_ppi_current_book.py")
ENGINE_GIT_BLOB = "eb7387f1409b990214e56d1cf04f44381c9b5be9"
PRODUCT_SHA = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"
PRODUCT_CALENDAR_ROOT = Path("_audited_product_calendar")
CALENDAR_GIT_BLOB = "add88988456ba8d327c0a239d1172a9a45b36bc2"
SESSIONS_GIT_BLOB = "bfcaf3909b5afde2a3e0e2f56b3aeaefc047d422"
BYMA_OFFICIAL_CALENDAR_SOURCE = "https://www.byma.com.ar/mercado/calendario-bursatil"
CALENDAR_AUDIT_MAX_AGE_DAYS = 60  # same bounded freshness policy as RC6 Market Watch
# Exact historical product calendar reasons corresponding to BYMA footnote (3):
# negotiated session, but NO settlement. Only diagnostic PPI Current/Book GET
# under an explicitly bounded budget is eligible; never PAPER entry or settlement.
LIMITED_NO_SETTLEMENT_REASONS = frozenset({
    "Día del Bancario; jornada especial sin liquidación",
    "Día no laborable con fines turísticos",
    "Nochebuena; jornada especial sin liquidación",
})
SESSION_REGULAR = "BYMA_AUDITED_OPEN"
SESSION_LIMITED = "BYMA_LIMITED_TRADING_NO_SETTLEMENT_OBSERVE_ONLY"
SESSION_POSTCLOSE = "BYMA_CLOSED_POSTCLOSE_DIAGNOSTIC_ONLY"
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


def pinned_product_calendar_files(root: Path,
                                  calendar_blob: str = CALENDAR_GIT_BLOB,
                                  sessions_blob: str = SESSIONS_GIT_BLOB) -> bool:
    """Independent exact object pins; a future calendar update requires review."""
    return (pinned_engine_ok(root / "ak_byma_calendar.py", calendar_blob)
            and pinned_engine_ok(root / "co_market_sessions_hf6.py", sessions_blob))


def audited_market_session(now_utc: datetime, calendar, sessions):
    """Conservative source, audited year/age, holiday and spot-hours gate.

    This is a DAY/SESSION test only. It does NOT authorize a provider GET or
    certify PPI identity/PIT, and intentionally has no permanent expiry day.
    """
    try:
        if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
            return False, "MARKET_CALENDAR_NOT_VERIFIED"
        art = now_utc.astimezone(ZONE)
        audit = calendar.CALENDARIO_ACTUALIZADO_EL
        years = calendar.ANIOS_AUDITADOS
        if (calendar.FUENTE_OFICIAL != BYMA_OFFICIAL_CALENDAR_SOURCE
                or not isinstance(audit, date) or isinstance(audit, datetime)
                or not isinstance(years, (set, frozenset))
                or art.year not in years
                or not 0 <= (art.date() - audit).days <= CALENDAR_AUDIT_MAX_AGE_DAYS
                or sessions.TZ != ZONE
                or not isinstance(sessions.BYMA_PAPER_SPOT_OPEN, time)
                or not isinstance(sessions.BYMA_PAPER_SPOT_CLOSE, time)):
            return False, "MARKET_CALENDAR_NOT_VERIFIED"
        current = art.time().replace(tzinfo=None)
        if not sessions.BYMA_PAPER_SPOT_OPEN <= current < sessions.BYMA_PAPER_SPOT_CLOSE:
            return False, "OUTSIDE_AUDITED_BYMA_SPOT_WINDOW"
        if calendar.es_dia_habil_operativo(art.date()) is True:
            return True, SESSION_REGULAR
        # The audited product calendar conservatively marks every non-normal
        # session non-operational. BYMA's distinct footnote (3) nevertheless
        # permits NEGOTIATION without SETTLEMENT. This PPI diagnostic is
        # READ_ONLY evidence, not a settlement/entry-capability decision.
        reason = calendar.motivo_no_operativo(art.date())
        if type(reason) is str and reason in LIMITED_NO_SETTLEMENT_REASONS:
            return True, SESSION_LIMITED
        return False, "BYMA_NO_NEGOTIATION_OR_SPECIAL_SESSION_HOLD"
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return False, "MARKET_CALENDAR_NOT_VERIFIED"


def audited_postclose_window(now_utc: datetime, calendar, sessions):
    """Manual 17:00-23:00 ART date/audit gate; NEVER a market-open grant.

    This does not attest provider quota or freshness. On any missing official
    calendar pin/stale source, return HOLD before contacting the droplet.
    """
    try:
        if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
            return False, "MARKET_CALENDAR_NOT_VERIFIED"
        art = now_utc.astimezone(ZONE)
        audit = calendar.CALENDARIO_ACTUALIZADO_EL
        if (calendar.FUENTE_OFICIAL != BYMA_OFFICIAL_CALENDAR_SOURCE
                or not isinstance(audit, date) or isinstance(audit, datetime)
                or not isinstance(calendar.ANIOS_AUDITADOS, (set, frozenset))
                or art.year not in calendar.ANIOS_AUDITADOS
                or not 0 <= (art.date() - audit).days <= CALENDAR_AUDIT_MAX_AGE_DAYS
                or sessions.TZ != ZONE
                or not isinstance(sessions.BYMA_PAPER_SPOT_CLOSE, time)):
            return False, "MARKET_CALENDAR_NOT_VERIFIED"
        minutes = art.hour * 60 + art.minute
        if (art.weekday() >= 5 or not 17 * 60 <= minutes < 23 * 60
                or art.time().replace(tzinfo=None) < sessions.BYMA_PAPER_SPOT_CLOSE):
            return False, "OUTSIDE_POSTCLOSE_DIAGNOSTIC_WINDOW"
        # Outside spot hours is NOT proof that the day had a regular audited
        # negotiation session. The pinned product calendar explicitly excludes
        # Nov 6 limited-no-settlement and Nov 9/10 no-negotiation days. Do not
        # use their stale quotes to mint a diagnostic GET entitlement.
        operational = calendar.es_dia_habil_operativo(art.date())
        reason = calendar.motivo_no_operativo(art.date())
        if operational is False and type(reason) is str:
            return (False, "BYMA_LIMITED_NO_SETTLEMENT_POSTCLOSE_HOLD"
                    if reason in LIMITED_NO_SETTLEMENT_REASONS else
                    "BYMA_NO_NEGOTIATION_OR_SPECIAL_SESSION_HOLD")
        if operational is not True or reason is not None:
            return False, "MARKET_CALENDAR_NOT_VERIFIED"
        return True, SESSION_POSTCLOSE
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return False, "MARKET_CALENDAR_NOT_VERIFIED"


def _verified_product_checkout(root: Path) -> bool:
    """No source from a moving product branch is accepted into the day gate."""
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            text=True, stderr=subprocess.DEVNULL, timeout=3).strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return sha == PRODUCT_SHA and pinned_product_calendar_files(root)


def _load_product_module(root: Path, file_name: str, tag: str):
    """Execute ONLY a separately exact-blob-pinned, audited product module."""
    path = root / file_name
    spec = importlib.util.spec_from_file_location("_rc6_a670_" + tag, path)
    if spec is None or spec.loader is None:
        raise ValueError("PINNED_CALENDAR_IMPORT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(spec.name, None)


def _official_session_check(now_utc: datetime, root: Path):
    if not _verified_product_checkout(root):
        return False, "PINNED_PRODUCT_CALENDAR_UNAVAILABLE"
    try:
        calendar = _load_product_module(root, "ak_byma_calendar.py", "byma_calendar")
        sessions = _load_product_module(root, "co_market_sessions_hf6.py", "market_sessions")
        return audited_market_session(now_utc, calendar, sessions)
    except (OSError, ValueError, ImportError, TypeError, RuntimeError):
        return False, "MARKET_CALENDAR_NOT_VERIFIED"


def _official_postclose_check(now_utc: datetime, root: Path):
    if not _verified_product_checkout(root):
        return False, "PINNED_PRODUCT_CALENDAR_UNAVAILABLE"
    try:
        calendar = _load_product_module(root, "ak_byma_calendar.py", "byma_calendar")
        sessions = _load_product_module(root, "co_market_sessions_hf6.py", "market_sessions")
        return audited_postclose_window(now_utc, calendar, sessions)
    except (OSError, ValueError, ImportError, TypeError, RuntimeError):
        return False, "MARKET_CALENDAR_NOT_VERIFIED"


def eligible_slot(*, now_utc: datetime, event: str, schedule: str,
                  manual_slot: str, repository: str, ref: str,
                  checkout_sha: str, source_sha: str,
                  engine_path: Path, expected_engine_blob: str = ENGINE_GIT_BLOB):
    """Return (slot, reason); a nonempty slot is the ONLY permission to proceed.

    The caller must run this *before* reaching SSH or any provider/Watch route.
    Passing this time gate is never a PPI GET authorization: that additionally
    requires either the existing runtime budget's live leases or the strictly
    opt-in local pilot, which cannot confer production quota authority.
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
    if event != "workflow_dispatch":
        return "", "MANUAL_DISPATCH_REQUIRED"
    if art.weekday() >= 5:
        return "", "ART_BUSINESS_WEEKDAY_REQUIRED"
    if schedule:
        return "", "SCHEDULE_INPUT_FORBIDDEN"
    slot = manual_slot if manual_slot in SLOT_MINUTES else ""
    if not slot:
        return "", "UNKNOWN_MANUAL_SLOT"
    observed_minute = art.hour * 60 + art.minute
    if not SLOT_MINUTES[slot] <= observed_minute <= SLOT_MINUTES[slot] + 60:
        return "", "OUTSIDE_ART_SLOT_WINDOW"
    if not pinned_engine_ok(engine_path, expected_engine_blob):
        return "", "AUDITED_ENGINE_BLOB_MISMATCH"
    return slot, "PASS_WEEKDAY_SLOT_AND_ENGINE_ONLY"


def eligible_postclose(*, now_utc: datetime, event: str, manual_slot: str,
                       repository: str, ref: str, checkout_sha: str,
                       source_sha: str, engine_path: Path,
                       pilot_mode: str, expected_engine_blob: str = ENGINE_GIT_BLOB):
    """Additional opt-in manual gate; never authorizes a schedule event."""
    if (event != "workflow_dispatch" or pilot_mode != "ONCE"
            or repository != REPOSITORY or ref != CONTROL_REF
            or manual_slot not in SLOT_MINUTES):
        return "", "POSTCLOSE_MANUAL_PILOT_REQUIRED"
    if (not isinstance(checkout_sha, str) or not isinstance(source_sha, str)
            or HEX40.fullmatch(checkout_sha) is None or checkout_sha != source_sha):
        return "", "EXACT_CHECKOUT_SHA_REQUIRED"
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
        return "", "AWARE_UTC_CLOCK_REQUIRED"
    try:
        art = now_utc.astimezone(ZONE)
        minutes = art.hour * 60 + art.minute
        if art.weekday() >= 5 or not 17 * 60 <= minutes < 23 * 60:
            return "", "OUTSIDE_POSTCLOSE_DIAGNOSTIC_WINDOW"
    except (TypeError, ValueError, OverflowError):
        return "", "AWARE_UTC_CLOCK_REQUIRED"
    if not pinned_engine_ok(engine_path, expected_engine_blob):
        return "", "AUDITED_ENGINE_BLOB_MISMATCH"
    return manual_slot, "PASS_POSTCLOSE_MANUAL_ENGINE_ONLY"


def _append_verified_env(path: Path, slot: str, now_utc: datetime,
                         market_session: str = SESSION_REGULAR) -> bool:
    """Append only bounded constant-shape values after the immutable gate."""
    try:
        if market_session not in (SESSION_REGULAR, SESSION_LIMITED, SESSION_POSTCLOSE):
            return False
        if path.is_symlink() or not path.is_file():
            return False
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            payload = (f"RC6_A669_SLOT={slot}\n"
                       f"RC6_A669_ENGINE_BLOB={ENGINE_GIT_BLOB}\n"
                       f"RC6_A670_ART_DATE={now_utc.astimezone(ZONE).date().isoformat()}\n"
                       f"RC6_A670_MARKET_SESSION={market_session}\n"
                       + ("RC6_A670_POSTCLOSE=ONCE\n" if market_session == SESSION_POSTCLOSE
                          else "")).encode("ascii")
            return os.write(fd, payload) == len(payload)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False


def _append_hold_env(path: Path, reason: str, now_utc: datetime) -> bool:
    """Expose only a static-shaped HOLD label, never tokens, payload or URLs."""
    try:
        if not isinstance(reason, str) or re.fullmatch(r"[A-Z0-9_]{5,64}", reason) is None:
            return False
        if path.is_symlink() or not path.is_file():
            return False
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            payload = (f"RC6_A670_HOLD_REASON={reason}\n"
                       f"RC6_A670_ART_DATE={now_utc.astimezone(ZONE).date().isoformat()}\n").encode("ascii")
            return os.write(fd, payload) == len(payload)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False


def main() -> int:
    """CI-only entry point: time+calendar guard, no host/provider operation."""
    try:
        current_sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            text=True, timeout=3, stderr=subprocess.DEVNULL).strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        current_sha = ""
    now_utc = datetime.now(timezone.utc)
    postclose = os.environ.get("RC6_A630_POSTCLOSE_MODE") == "ONCE"
    common = dict(now_utc=now_utc,
        event=os.environ.get("GITHUB_EVENT_NAME", ""),
        manual_slot=os.environ.get("RC6_A669_MANUAL_SLOT", ""),
        repository=os.environ.get("GITHUB_REPOSITORY", ""),
        ref=os.environ.get("GITHUB_REF", ""),
        checkout_sha=current_sha,
        source_sha=os.environ.get("GITHUB_SHA", ""),
        engine_path=ENGINE_PATH)
    if postclose:
        slot, reason = eligible_postclose(**common,
            pilot_mode=os.environ.get("RC6_A630_PILOT_MODE", "OFF"))
    else:
        slot, reason = eligible_slot(**common,
            schedule="")
    market_session = ""
    if slot:
        allowed, calendar_reason = (
            _official_postclose_check(now_utc, PRODUCT_CALENDAR_ROOT) if postclose
            else _official_session_check(now_utc, PRODUCT_CALENDAR_ROOT))
        if not allowed:
            slot, reason = "", calendar_reason
        else:
            market_session = calendar_reason
    env_path = Path(os.environ.get("GITHUB_ENV", ""))
    if not slot:
        _append_hold_env(env_path, reason, now_utc)
        print(f"::warning title=A670 PPI offline gate HOLD::{reason}; zero provider GET; check audited market calendar or slot before bounded fallback")
        print(f"A670_GUARD=HOLD|REASON={reason}|PPI_GETS_BY_GUARD=0")
        return 2
    if not _append_verified_env(env_path, slot, now_utc, market_session):
        print("A670_GUARD=HOLD|REASON=ENV_WRITE_DENIED|PPI_GETS_BY_GUARD=0")
        return 2
    print(f"A670_GUARD=PASS|SLOT={slot}|ART_DATE={now_utc.astimezone(ZONE).date()}|SESSION={market_session}|PAPER_ENTRY=NO|PPI_GETS_BY_GUARD=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
