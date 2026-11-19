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
import select
import stat
import subprocess
import sys
import time as monotonic_time
from zoneinfo import ZoneInfo


WORKSTREAM = "A670"
ZONE = ZoneInfo("America/Argentina/Buenos_Aires")
REPOSITORY = "mbalbo2023/Porota-trading"
CONTROL_REF = "refs/heads/main"
ENGINE_PATH = Path("scripts/rc6_a630_host_ppi_current_book.py")
ENGINE_GIT_BLOB = "08c88dab13ee17d1dff6e2414a242003cc30883f"
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
OBSERVER_COMMAND_FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
})
# P0 release is valid PAPER runtime but its scanner lacks the new OPEN handler.
# Only a *new* tested Deploy V2 source/tree/image/manifest repin may remove
# this NO-GO. The downstream rootless wrapper independently attests the exact
# physical release both before and after the one queue INSERT.
OPEN_HANDLER_ABSENT_P0_SHA = "b3cbe57a5b4b82016a65a0e253b251abbb84a04c"
OPEN_HANDLER_ABSENT_P0_TREE = "7370fea6c9c6daa802fcdd8ce6fc20c5cf8e31b0"


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


def eligible_observer_command(*, now_utc: datetime, event: str,
                              repository: str, ref: str, checkout_sha: str,
                              source_sha: str, art_date: str, family: str,
                              pilot_mode: str, postclose_mode: str,
                              engine_path: Path,
                              expected_engine_blob: str = ENGINE_GIT_BLOB):
    """Manual-only command issuer, independent of the legacy sample slots.

    This gate never imports a broker, touches the host or grants a GET. The
    independently pinned BYMA business-day calendar is applied in main().
    """
    if event != "workflow_dispatch":
        return "", "MANUAL_DISPATCH_REQUIRED"
    if repository != REPOSITORY or ref != CONTROL_REF:
        return "", "NONCANONICAL_REPOSITORY_OR_REF"
    if (not isinstance(checkout_sha, str) or not isinstance(source_sha, str)
            or HEX40.fullmatch(checkout_sha) is None or checkout_sha != source_sha):
        return "", "EXACT_CHECKOUT_SHA_REQUIRED"
    if (not isinstance(art_date, str) or
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", art_date) is None):
        return "", "OBSERVER_COMMAND_ART_DATE_INVALID"
    try:
        if date.fromisoformat(art_date).isoformat() != art_date:
            return "", "OBSERVER_COMMAND_ART_DATE_INVALID"
    except ValueError:
        return "", "OBSERVER_COMMAND_ART_DATE_INVALID"
    if type(family) is not str or family not in OBSERVER_COMMAND_FAMILIES:
        return "", "OBSERVER_COMMAND_FAMILY_INVALID"
    if pilot_mode != "OFF" or postclose_mode != "OFF":
        return "", "OBSERVER_COMMAND_SAMPLE_MODE_FORBIDDEN"
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
        return "", "AWARE_UTC_CLOCK_REQUIRED"
    try:
        art = now_utc.astimezone(ZONE)
    except (ValueError, OverflowError):
        return "", "AWARE_UTC_CLOCK_REQUIRED"
    if art.date().isoformat() != art_date:
        return "", "OBSERVER_COMMAND_ART_DATE_MISMATCH"
    minute = art.hour * 60 + art.minute
    if art.weekday() >= 5 or not 17 * 60 <= minute < 23 * 60:
        return "", "OUTSIDE_POSTCLOSE_DIAGNOSTIC_WINDOW"
    if not pinned_engine_ok(engine_path, expected_engine_blob):
        return "", "AUDITED_ENGINE_BLOB_MISMATCH"
    return (f"PPI_DIRECT_REST_POSTCLOSE_ONCE:{art_date}:{family}",
            "PASS_MANUAL_POSTCLOSE_OBSERVER_COMMAND_ONLY")


def eligible_open_observer_command(*, now_utc: datetime, event: str,
                                   repository: str, ref: str, checkout_sha: str,
                                   source_sha: str, art_date: str, family: str,
                                   pilot_mode: str, postclose_mode: str,
                                   engine_path: Path,
                                   expected_engine_blob: str = ENGINE_GIT_BLOB):
    """One in-scanner, ten-family OPEN QA command; never a GET entitlement."""
    if event != "workflow_dispatch":
        return "", "MANUAL_DISPATCH_REQUIRED"
    if repository != REPOSITORY or ref != CONTROL_REF:
        return "", "NONCANONICAL_REPOSITORY_OR_REF"
    if (not isinstance(checkout_sha, str) or not isinstance(source_sha, str)
            or HEX40.fullmatch(checkout_sha) is None or checkout_sha != source_sha):
        return "", "EXACT_CHECKOUT_SHA_REQUIRED"
    if (not isinstance(art_date, str) or
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", art_date) is None):
        return "", "OBSERVER_COMMAND_ART_DATE_INVALID"
    try:
        if date.fromisoformat(art_date).isoformat() != art_date:
            return "", "OBSERVER_COMMAND_ART_DATE_INVALID"
    except ValueError:
        return "", "OBSERVER_COMMAND_ART_DATE_INVALID"
    if family != "ALL":
        return "", "OBSERVER_OPEN_ALL_ONLY"
    if pilot_mode != "OFF" or postclose_mode != "OFF":
        return "", "OBSERVER_COMMAND_SAMPLE_MODE_FORBIDDEN"
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
        return "", "AWARE_UTC_CLOCK_REQUIRED"
    try:
        art = now_utc.astimezone(ZONE)
    except (ValueError, OverflowError):
        return "", "AWARE_UTC_CLOCK_REQUIRED"
    if art.date().isoformat() != art_date:
        return "", "OBSERVER_COMMAND_ART_DATE_MISMATCH"
    minute = art.hour * 60 + art.minute
    if art.weekday() >= 5 or not 10 * 60 + 30 <= minute < 17 * 60:
        return "", "OUTSIDE_AUDITED_BYMA_SPOT_WINDOW"
    if not pinned_engine_ok(engine_path, expected_engine_blob):
        return "", "AUDITED_ENGINE_BLOB_MISMATCH"
    return (f"PPI_DIRECT_REST_OPEN_ONCE:{art_date}:ALL",
            "PASS_MANUAL_OPEN_TEN_FAMILY_OBSERVER_COMMAND_ONLY")


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


def _append_observer_command_env(path: Path, command: str, now_utc: datetime,
                                 market_session: str = SESSION_POSTCLOSE,
                                 promote_claim=None) -> bool:
    """Pass GitHub-artifact pins, not untrusted dispatch/env release values, to SSH.

    A passing OFFHOST gate is only permission to attempt independent *physical*
    rootless matching on the host. No singleton/PPI claim comes from Actions.
    """
    fields = command.split(":")
    allowed = ((fields[0] == "PPI_DIRECT_REST_POSTCLOSE_ONCE" and
                fields[2] in OBSERVER_COMMAND_FAMILIES and
                market_session == SESSION_POSTCLOSE) or
               (fields[0] == "PPI_DIRECT_REST_OPEN_ONCE" and fields[2] == "ALL" and
                market_session == SESSION_REGULAR)) if len(fields) == 3 else False
    if (len(fields) != 3 or not allowed or not _safe_promote_claim(promote_claim) or
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields[1]) or
            fields[1] != now_utc.astimezone(ZONE).date().isoformat()):
        return False
    try:
        if path.is_symlink() or not path.is_file():
            return False
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            payload = (f"RC6_A670_OBSERVER_COMMAND={command}\n"
                       f"RC6_A670_ART_DATE={fields[1]}\n"
                       f"RC6_A670_FAMILY={fields[2]}\n"
                       f"RC6_A670_MARKET_SESSION={market_session}\n"
                       f"RC6_P0_EXPECTED_CANDIDATE_SHA={promote_claim['source_sha']}\n"
                       f"RC6_P0_EXPECTED_TREE_SHA={promote_claim['source_tree']}\n"
                       f"RC6_P0_EXPECTED_IMAGE_ID={promote_claim['image_id']}\n"
                       f"RC6_P0_EXPECTED_RELEASE_MANIFEST_SHA256={promote_claim['release_manifest_sha256']}\n"
                       f"RC6_P0_EXPECTED_OBSERVER_CID={promote_claim['observer_cid']}\n"
                       f"RC6_P0_EXPECTED_DASHBOARD_CID={promote_claim['dashboard_cid']}\n"
                       f"RC6_A670_PROMOTE_ARTIFACT_DIGEST={promote_claim['runtime_artifact_digest']}\n").encode("ascii")
            return os.write(fd, payload) == len(payload)
        finally:
            os.close(fd)
    except (OSError, ValueError, UnicodeError):
        return False


def _safe_promote_claim(claim):
    """Shape-only defense; cryptographic verification lives in the A630 reader."""
    if type(claim) is not dict or set(claim) != {
            "source_sha", "source_tree", "image_id", "release_manifest_sha256",
            "observer_cid", "dashboard_cid", "promote_run_id",
            "promote_run_attempt", "runtime_artifact_digest"}:
        return False
    expressions = {"source_sha": r"[0-9a-f]{40}",
                   "source_tree": r"[0-9a-f]{40}",
                   "image_id": r"sha256:[0-9a-f]{64}",
                   "release_manifest_sha256": r"[0-9a-f]{64}",
                   "observer_cid": r"[0-9a-f]{64}",
                   "dashboard_cid": r"[0-9a-f]{64}",
                   "runtime_artifact_digest": r"sha256:[0-9a-f]{64}"}
    return (all(type(claim[key]) is str and
                re.fullmatch(pattern, claim[key]) is not None
                for key, pattern in expressions.items()) and
            claim["observer_cid"] != claim["dashboard_cid"] and
            all(type(claim[key]) is int and claim[key] > 0 for key in
                ("promote_run_id", "promote_run_attempt")))


def _bounded_gh_api(endpoint, *, max_bytes):
    """Read GH REST without ever buffering more than the reviewed byte cap.

    A malicious server or redirect can exceed GitHub's claimed artifact size:
    unlike `subprocess.run(capture_output=True)`, this reader stops and kills
    the child as soon as the cap is exceeded or the wall-clock deadline passes.
    """
    hold = "PROMOTE_ARTIFACT_CUSTODY_HOLD"
    if (type(endpoint) is not str or len(endpoint) > 240 or
            not endpoint.startswith("repos/mbalbo2023/Porota-trading/actions/") or
            re.fullmatch(r"[A-Za-z0-9/_%?=&.\-]+", endpoint) is None or
            type(max_bytes) is not int or not 0 < max_bytes <= 262144):
        raise ValueError(hold)
    process = None
    try:
        process = subprocess.Popen(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", endpoint],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        deadline = monotonic_time.monotonic() + 15
        output = bytearray()
        while True:
            remaining = deadline - monotonic_time.monotonic()
            if remaining <= 0:
                raise ValueError(hold)
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if not readable:
                raise ValueError(hold)
            part = os.read(process.stdout.fileno(), min(8192, max_bytes + 1 - len(output)))
            if not part:
                break
            output.extend(part)
            if len(output) > max_bytes:
                raise ValueError(hold)
        if process.wait(timeout=max(0, deadline - monotonic_time.monotonic())) != 0 or not output:
            raise ValueError(hold)
        return bytes(output)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise ValueError(hold) from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            if process.stdout is not None:
                process.stdout.close()


def _trusted_promote_claim():
    """Fetch GitHub's verified immutable runtime receipt over Actions-token API.

    Never accept a workflow input, env pin, bare success flag or old run as
    authority. The A630 validator parses/downloads both artifact JSON files.
    """
    if not os.environ.get("GH_TOKEN") or not pinned_engine_ok(ENGINE_PATH):
        raise ValueError("PROMOTE_ARTIFACT_CUSTODY_HOLD")
    # Direct script execution (`python scripts/guard.py`) does not guarantee
    # that the repository root is on sys.path. Import exactly the blob-pinned
    # local engine via a file-location spec, never import an unpinned package.
    spec = importlib.util.spec_from_file_location("rc6_a630_reviewed_engine", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise ValueError("PROMOTE_ARTIFACT_CUSTODY_HOLD")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def read_api(endpoint):
        return _bounded_gh_api(endpoint,
                               max_bytes=65536 if endpoint.endswith("/zip") else 262144)

    claim = module.fetch_latest_verified_promote_v2_claim(read_api)
    if not _safe_promote_claim(claim):
        raise ValueError("PROMOTE_ARTIFACT_CUSTODY_HOLD")
    return claim


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


def main(now_utc=None) -> int:
    """CI-only entry point: time+calendar guard, no host/provider operation."""
    try:
        current_sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            text=True, timeout=3, stderr=subprocess.DEVNULL).strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        current_sha = ""
    now_utc = datetime.now(timezone.utc) if now_utc is None else now_utc
    issuer_mode = os.environ.get("RC6_A670_ISSUER_MODE", "sample")
    if issuer_mode == "sample" and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        # The archived capture script could instantiate another PPI reader.
        # Keep its historical tests, but prohibit its Actions execution.
        print("A670_GUARD=HOLD|REASON=LEGACY_SECOND_PPI_SESSION_FORBIDDEN|PPI_GETS_BY_GUARD=0")
        return 2
    if issuer_mode in {"observer_postclose_command", "observer_open_ten_family_command"}:
        open_mode = issuer_mode == "observer_open_ten_family_command"
        issuer_gate = (eligible_open_observer_command if open_mode else
                       eligible_observer_command)
        command, reason = issuer_gate(
            now_utc=now_utc, event=os.environ.get("GITHUB_EVENT_NAME", ""),
            repository=os.environ.get("GITHUB_REPOSITORY", ""),
            ref=os.environ.get("GITHUB_REF", ""), checkout_sha=current_sha,
            source_sha=os.environ.get("GITHUB_SHA", ""),
            art_date=os.environ.get("RC6_A670_OBSERVER_ART_DATE", ""),
            family=os.environ.get("RC6_A670_OBSERVER_FAMILY", ""),
            pilot_mode=os.environ.get("RC6_A630_PILOT_MODE", ""),
            postclose_mode=os.environ.get("RC6_A630_POSTCLOSE_MODE", ""),
            engine_path=ENGINE_PATH)
        if command:
            allowed, session = ((_official_session_check(now_utc, PRODUCT_CALENDAR_ROOT)
                                 if open_mode else
                                 _official_postclose_check(now_utc, PRODUCT_CALENDAR_ROOT)))
            if not allowed:
                command, reason = "", session
            elif open_mode and session != SESSION_REGULAR:
                command, reason = "", "BYMA_LIMITED_NO_SETTLEMENT_OPEN_HOLD"
            else:
                try:
                    promote_claim = _trusted_promote_claim()
                except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                    command, reason = "", "OBSERVER_COMMAND_RELEASE_UNPINNED"
                else:
                    if (promote_claim["source_sha"] == OPEN_HANDLER_ABSENT_P0_SHA or
                            promote_claim["source_tree"] == OPEN_HANDLER_ABSENT_P0_TREE):
                        # Historical P0 is VALIDADO_RUNTIME for its own PAPER
                        # workload; it has neither the new OPEN handler nor
                        # physically proved three-role Gateway custody.
                        command, reason = "", ("OBSERVER_OPEN_PRODUCT_DEPLOY_UNVERIFIED"
                                               if open_mode else
                                               "OBSERVER_COMMAND_RELEASE_UNPINNED")
                    elif not _safe_promote_claim(promote_claim):
                        command, reason = "", "OBSERVER_COMMAND_RELEASE_UNPINNED"
        env_path = Path(os.environ.get("GITHUB_ENV", ""))
        if not command:
            _append_hold_env(env_path, reason, now_utc)
            print(f"A670_OBSERVER_COMMAND=HOLD|REASON={reason}|PPI_CLIENT_OR_LOGIN_BY_ACTION=0")
            return 2
        if not _append_observer_command_env(env_path, command, now_utc,
                                           SESSION_REGULAR if open_mode else SESSION_POSTCLOSE,
                                           promote_claim):
            print("A670_OBSERVER_COMMAND=HOLD|REASON=ENV_WRITE_DENIED|PPI_CLIENT_OR_LOGIN_BY_ACTION=0")
            return 2
        print(f"A670_OBSERVER_COMMAND=PASS|ART_DATE={now_utc.astimezone(ZONE).date()}|"
              f"SESSION={'OPEN_QA_ONLY' if open_mode else 'POSTCLOSE_DIAGNOSTIC_ONLY'}|"
              "PPI_CLIENT_OR_LOGIN_BY_ACTION=0")
        return 0
    if issuer_mode != "sample":
        print("A670_GUARD=HOLD|REASON=UNKNOWN_ISSUER_MODE|PPI_GETS_BY_GUARD=0")
        return 2
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
