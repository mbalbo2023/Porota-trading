"""Durable, release-bound preopen interlock for live RC6 PAPER admissions.

The preopen service publishes one small receipt after its read-only probes.
Consumers fail closed on missing, stale or mismatched receipts. This module
contains no order client and never authorizes a real broker route.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from scripts.rc6_operator_paper_rearm import (
    OPERATOR_DIR_HOST, OPERATOR_DIR_RUNTIME, evaluate_rearm,
    rootless_host_operator_directory, _validated_paper_release,
)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
HOST_ROOT = Path("/opt/porota-trading")
HOST_RECEIPT = HOST_ROOT / "data/deploy/paper_scalping_preopen_gate_v1.json"
RUNTIME_RECEIPT = Path("/app/data/deploy/paper_scalping_preopen_gate_v1.json")
# The v2 authority stays outside DATA and is read-only in the container, where
# its owner is namespace UID zero. Host rootless publication uses the SSH UID.
HOST_V2_RECEIPT = OPERATOR_DIR_HOST / "paper-preopen-v2.json"
RUNTIME_V2_RECEIPT = OPERATOR_DIR_RUNTIME / "paper-preopen-v2.json"
REVALIDATION_NAME = "paper-preopen-release-v2.json"
REVALIDATION_PHASE = "POST_DEPLOY_REVALIDATION"
REVALIDATION_SCHEMA = "RC6_PAPER_POST_DEPLOY_GATE_V1"
RECOVERY_PHASE = "INTRADAY_T10_RECOVERY"
TECHNICAL_T10_HOLD = "PREOPEN_OR_RELEASE_NOT_GREEN"
IOL_PROVIDER = "IOL_REST"
RECOVERY_PROBE_MAX_AGE_SECONDS = 120
# An operational sampling preference is never an admission whitelist. Keep
# diagnostics bounded independently of the configured sampling universe.
CATALOG_DIAGNOSTIC_LIMIT = 64
SHA = re.compile(r"[0-9a-f]{40}\Z")
# Historical date-scoped receipts remain immutable. The later operator mission
# explicitly keeps NO_GO until a separate, specific reactivation. A successful
# deploy or a future T-10 receipt cannot silently turn PAPER admissions on.
OPERATOR_PAUSED_TRADE_DAYS_AR = frozenset({"2026-10-27", "2026-10-28"})
NO_GO_UNTIL_EXPLICIT_REARM_FROM_AR = "2026-10-29"


def operator_pause_reason(now, *, context="runtime"):
    # Every operator day is an Argentine business day, including direct helper
    # callers passing UTC at 00:00-02:59. Never classify by the caller's zone.
    if now.tzinfo is None or now.utcoffset() is None:
        return "OPERATOR_PAUSE_CLOCK_INVALID"
    now = now.astimezone(TZ)
    day = now.date().isoformat()
    if day in OPERATOR_PAUSED_TRADE_DAYS_AR:
        return "OPERATOR_PAPER_PAUSE_" + day.replace("-", "_")
    if day >= NO_GO_UNTIL_EXPLICIT_REARM_FROM_AR:
        authorized, reason, _ = evaluate_rearm(now=now, context=context)
        return "" if authorized else reason
    return ""


def focus_contracts(db_path):
    """Bounded catalog *advisory*, retained under its legacy entry-point name.

    The preopen receipt is infrastructure/release authority, not a vote on a
    fixed group of unrelated tickers. Contract readiness belongs to the exact
    quote's final PAPER admission. This probe never grants quote readiness.
    """
    advisory = {"effect": "ADVISORY_ONLY", "scope": "BOUNDED_CATALOG_SAMPLE",
                "sample_limit": CATALOG_DIAGNOSTIC_LIMIT}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            names = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "name IN ('financial_instrument_catalog','candidate_identity_v2')")}
            if names != {"financial_instrument_catalog", "candidate_identity_v2"}:
                return {**advisory, "state": "AMBER",
                        "reason": "CATALOG_OR_EXACT_IDENTITY_LEDGER_MISSING"}
            # The LEFT JOIN is over *catalog rows*, rather than a fixed ticker
            # basket. Group by rowid to detect duplicate/missing exact ledger
            # identities without unbounded result materialization.
            rows = db.execute("""WITH sampled AS (
                  SELECT rowid AS rid, ticker,instrument_type,market,currency,
                         settlement,status,capability
                  FROM financial_instrument_catalog
                  ORDER BY rowid LIMIT ?)
                SELECT f.instrument_type,f.status,f.capability,
                       COUNT(c.ticker) AS identity_rows,
                       SUM(CASE WHEN c.status='AVAILABLE' AND c.can_simulate=1
                                THEN 1 ELSE 0 END) AS ready_identity_rows
                FROM sampled f LEFT JOIN candidate_identity_v2 c
                  ON f.ticker=c.ticker AND f.instrument_type=c.instrument_type
                 AND f.market=c.market AND f.currency=c.currency
                 AND f.settlement=c.settlement
                GROUP BY f.rid ORDER BY f.rid""",
                (CATALOG_DIAGNOSTIC_LIMIT + 1,)).fetchall()
            truncated = len(rows) > CATALOG_DIAGNOSTIC_LIMIT
            sampled = rows[:CATALOG_DIAGNOSTIC_LIMIT]
            ready = 0
            not_ready_by_family = {}
            for family, status, capability, identities, ready_identities in sampled:
                expected = {"FUTUROS": "READY_PAPER_FUTURES",
                            "OPCIONES": "READY_PAPER_OPTION_LONG",
                            "CAUCIONES": "READY_PAPER_CAUCION_PLACING",
                            "FCI": "READY_PAPER_FCI_SUBSCRIPTION"}.get(
                                family, "READY_PAPER_SPOT")
                if (status == "AVAILABLE" and capability == expected
                        and identities == ready_identities == 1):
                    ready += 1
                else:
                    not_ready_by_family[family] = not_ready_by_family.get(family, 0) + 1
            return {**advisory, "state": ("GREEN" if sampled and not truncated
                                            and ready == len(sampled) else "AMBER"),
                    "sampled": len(sampled), "ready": ready,
                    "truncated": truncated,
                    "not_ready_by_family": not_ready_by_family}
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {**advisory, "state": "AMBER", "reason": type(exc).__name__}


def publish_preopen(result, *, now=None, state_path=None, receipt_path=None):
    """Atomically publish a closed receipt, opening it only for a full T-10 GO."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    canonical_state = HOST_ROOT / "data/deploy/CURRENT_STATE_V2.json"
    private = rootless_host_operator_directory()
    if private is not None:
        expected = private / "paper-preopen-v2.json"
        if ((receipt_path is not None and Path(receipt_path) != expected) or
                (state_path is not None and Path(state_path) != canonical_state)):
            raise ValueError("PAPER_ROOTLESS_CANONICAL_PATH_REQUIRED")
        receipt_path = expected
    state_path = Path(state_path or canonical_state)
    receipt_path = Path(receipt_path or (HOST_V2_RECEIPT if
                        now.date().isoformat() >= NO_GO_UNTIL_EXPLICIT_REARM_FROM_AR
                        else HOST_RECEIPT))
    candidate = ""
    try:
        if private is not None:
            candidate, _, _ = _validated_paper_release(
                marker_path=state_path, frozen_path=None, context="host")
            valid_release = True
        else:
            if state_path.is_symlink() or state_path.stat().st_size > 65536:
                raise ValueError("UNSAFE_RELEASE_STATE")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            candidate = state.get("candidate_sha", "")
            valid_release = (isinstance(candidate, str) and SHA.fullmatch(candidate)
                             and state.get("validation_status") == "VALIDATED_RUNTIME"
                             and state.get("mode") == "PRODUCTION_PAPER"
                             and type(state.get("real_orders_sent")) is int
                             and state["real_orders_sent"] == 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        valid_release = False
    pause_reason = operator_pause_reason(now, context="host")
    allowed = (not pause_reason and result.get("status") == "GREEN" and result.get("phase") == "T_MINUS_10"
               and 10 * 60 + 20 <= now.hour * 60 + now.minute < 10 * 60 + 30
               and valid_release)
    receipt = {"schema": ("RC6_PAPER_PREOPEN_GATE_V2" if
               now.date().isoformat() >= NO_GO_UNTIL_EXPLICIT_REARM_FROM_AR
               else "RC6_PAPER_PREOPEN_GATE_V1"), "status":
               "GO_PAPER_SCALPING" if allowed else "NO_GO_PAPER_SCALPING",
               "candidate_sha": candidate if valid_release else "", "trade_day_ar": now.date().isoformat(),
               "issued_at_ar": now.isoformat(timespec="seconds"), "phase": result.get("phase"),
               "reason": (pause_reason if pause_reason else
                          "PREOPEN_GREEN_RELEASE_PAPER_ZERO" if allowed else TECHNICAL_T10_HOLD)}
    if os.getenv("POROTA_RC6_MARKET_PROVIDER") == "IOL_REST":
        receipt["market_provider"] = "IOL_REST"
        if result.get("checks", {}).get("observer_db", {}).get("market_provider") != "IOL_REST":
            receipt.update(status="NO_GO_PAPER_SCALPING", reason="PAPER_PREOPEN_PROVIDER_MISMATCH")
    # A125's 27-Oct LIVE gate is an immutable link in the A138/A143 scheduler
    # recovery chain. Neither a manual preopen nor a subsequent day's T-10
    # may overwrite it while that chain is active. The separate A151 sidecar
    # attests today's NO_GO; any future rearm requires a reviewed migration.
    if (receipt_path == HOST_RECEIPT and
            now.date().isoformat() >= "2026-10-27"):
        raise ValueError("HISTORICAL_A125_LIVE_GATE_IMMUTABLE")
    return _write_gate(receipt_path, receipt, private=private is not None)


def _write_gate(receipt_path, receipt, *, private):
    if receipt_path.parent.is_symlink() or not receipt_path.parent.is_dir() or receipt_path.is_symlink():
        raise ValueError("UNSAFE_GATE_PATH")
    if private:
        from scripts.porota_operator_intent import private_read
        if os.path.lexists(receipt_path):
            private_read(receipt_path)
    elif receipt_path == HOST_V2_RECEIPT:
        observed = os.lstat(receipt_path.parent)
        if (os.geteuid() != 0 or observed.st_uid != 0 or
                observed.st_mode & 0o022):
            raise ValueError("PAPER_V2_ROOT_GATE_PATH_REQUIRED")
    fd, temporary = tempfile.mkstemp(prefix=".paper-gate-", dir=receipt_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600 if private else 0o644)
            json.dump(receipt, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, receipt_path)
        directory = os.open(receipt_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return receipt


def _gate_time(receipt, now):
    issued = datetime.fromisoformat(receipt["issued_at_ar"])
    if issued.tzinfo is None or issued.utcoffset() != now.utcoffset():
        raise ValueError("PREOPEN_CLOCK_INVALID")
    return issued.astimezone(TZ)


def _technical_t10_hold(receipt, provider):
    return (provider == IOL_PROVIDER and receipt.get("schema") == "RC6_PAPER_PREOPEN_GATE_V2"
            and receipt.get("status") == "NO_GO_PAPER_SCALPING"
            and receipt.get("reason") == TECHNICAL_T10_HOLD)


def _daily_gate_error(receipt, issued, now, *, provider, recover_technical_hold=False):
    expected_schema = ("RC6_PAPER_PREOPEN_GATE_V2" if
                       now.date().isoformat() >= NO_GO_UNTIL_EXPLICIT_REARM_FROM_AR
                       else "RC6_PAPER_PREOPEN_GATE_V1")
    recovery = recover_technical_hold and _technical_t10_hold(receipt, provider)
    if (receipt.get("schema") != expected_schema or receipt.get("phase") != "T_MINUS_10"
            or (receipt.get("status") != "GO_PAPER_SCALPING" and not recovery)):
        return "NO_GO_PAPER_SCALPING"
    # Legacy technical RED receipts can predate the provider field. A fresh
    # recovery binds IOL independently. An explicit mismatch still vetoes.
    recorded_provider = receipt.get("market_provider", provider if recovery else "PPI")
    if recorded_provider != provider:
        return "PAPER_PREOPEN_PROVIDER_MISMATCH"
    if receipt.get("trade_day_ar") != now.date().isoformat() or issued.date() != now.date():
        return "PAPER_PREOPEN_TRADE_DAY_MISMATCH"
    if (not 10 * 60 + 20 <= issued.hour * 60 + issued.minute < 10 * 60 + 30
            or not 0 <= (now - issued).total_seconds() <= 7 * 3600):
        return "PAPER_PREOPEN_WINDOW_INVALID"
    sha = receipt.get("candidate_sha")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        return "PAPER_PREOPEN_RECEIPT_INVALID"
    return ""


def _intraday_revalidation_window(at):
    return 10 * 60 + 30 <= at.hour * 60 + at.minute < 17 * 60


def _iol_recovery_proof(result, now):
    """Certify the installed IOL owner from the fresh, non-dry preopen probes."""
    observer = result.get("checks", {}).get("observer_db", {})
    state = observer.get("observer_state", {})
    try:
        checked = _gate_time({"issued_at_ar": result["generated_at_ar"]}, now)
    except (KeyError, TypeError, ValueError):
        return False
    return (result.get("schema") == "POROTA_RC6_PREOPEN_V3"
            and result.get("red_checks") == []
            and result.get("dry_equivalent") is False
            and 0 <= (now - checked).total_seconds() <= RECOVERY_PROBE_MAX_AGE_SECONDS
            and observer.get("state") == "GREEN"
            and observer.get("market_provider") == IOL_PROVIDER
            and observer.get("iol_owner_authenticated") is True
            and state.get("mode") == "PRODUCTION_PAPER"
            and state.get("session_state") == "MARKET_OPEN"
            and state.get("process_state") == "RUNNING"
            and type(state.get("real_orders_sent")) is int and state["real_orders_sent"] == 0)


def publish_release_revalidation(result, *, now, expected_source_sha):
    """Bind current live preopen checks to the original T−10 and current PAPER release."""
    now = now.astimezone(TZ)
    reject = {"status": "NO_GO_PAPER_SCALPING"}
    if not _intraday_revalidation_window(now):
        return {**reject, "reason": "PAPER_RELEASE_REVALIDATION_OUTSIDE_SESSION"}
    private = rootless_host_operator_directory()
    if private is None:
        return {**reject, "reason": "PAPER_RELEASE_REVALIDATION_ROOTLESS_REQUIRED"}
    authorized, reason, intent = evaluate_rearm(now=now, context="host")
    if not authorized:
        return {**reject, "reason": reason}
    if intent["candidate_sha"] != expected_source_sha:
        return {**reject, "reason": "PAPER_RELEASE_REVALIDATION_CANDIDATE_CHANGED"}
    from scripts.porota_operator_intent import private_read

    provider = os.getenv("POROTA_RC6_MARKET_PROVIDER", "PPI")
    try:
        daily_raw = private_read(private / HOST_V2_RECEIPT.name)
        daily = json.loads(daily_raw)
        issued = _gate_time(daily, now)
        reason = _daily_gate_error(daily, issued, now, provider=provider, recover_technical_hold=True)
    except (OSError, TypeError, KeyError, ValueError):
        return {**reject, "reason": "PAPER_RELEASE_REVALIDATION_DAILY_GATE_UNAVAILABLE"}
    if reason:
        return {**reject, "reason": reason}

    recovery = _technical_t10_hold(daily, provider)
    allowed = result.get("status") == "GREEN" and result.get("phase") == REVALIDATION_PHASE
    if provider == IOL_PROVIDER:
        allowed = allowed and result.get("checks", {}).get("observer_db", {}).get("market_provider") == IOL_PROVIDER
    if recovery:
        allowed = allowed and _iol_recovery_proof(result, now)
    elif daily["candidate_sha"] == expected_source_sha:
        return {"status": "GO_PAPER_SCALPING" if allowed else "NO_GO_PAPER_SCALPING",
                "reason": "PAPER_DAILY_GO_ALREADY_CURRENT" if allowed else TECHNICAL_T10_HOLD}
    receipt = {"schema": REVALIDATION_SCHEMA, "phase": RECOVERY_PHASE if recovery else REVALIDATION_PHASE,
               "status": "GO_PAPER_SCALPING" if allowed else "NO_GO_PAPER_SCALPING",
               "reason": (("PAPER_INTRADAY_PREOPEN_RECOVERED" if recovery else "PAPER_CURRENT_RELEASE_REVALIDATED")
                          if allowed else TECHNICAL_T10_HOLD),
               "candidate_sha": expected_source_sha, "trade_day_ar": now.date().isoformat(),
               "issued_at_ar": now.isoformat(timespec="seconds"),
               "daily_gate_sha256": hashlib.sha256(daily_raw).hexdigest(),
               "operator_run_id": intent["rearm_run_id"]}
    if provider == IOL_PROVIDER:
        receipt["market_provider"] = provider
    if recovery:
        receipt["candidate_tree_sha"] = intent["candidate_tree_sha"]
        receipt["image_id"] = intent["image_id"]
    return _write_gate(private / REVALIDATION_NAME, receipt, private=True)


def _read_gate(path, *, root_owned):
    if root_owned:
        parent = os.lstat(path.parent)
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022):
            raise ValueError("PAPER_V2_PREOPEN_ROOT_DIRECTORY_UNTRUSTED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        entry = os.fstat(fd)
        if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1
                or not 0 < entry.st_size <= 4096
                or (root_owned and (entry.st_uid != 0 or entry.st_mode & 0o022))):
            raise ValueError("PAPER_PREOPEN_RECEIPT_UNSAFE")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(4097)
        if len(raw) != entry.st_size:
            raise ValueError("PAPER_PREOPEN_RECEIPT_CHANGED")
    finally:
        os.close(fd)
    receipt = json.loads(raw)
    if not isinstance(receipt, dict):
        raise ValueError("PAPER_PREOPEN_RECEIPT_INVALID")
    return receipt, raw


def _valid_revalidation(redone, daily_raw, *, now, issued, provider, candidate, intent, recovery):
    revalidated_at = _gate_time(redone, now)
    return (intent["candidate_sha"] == candidate
            and redone.get("schema") == REVALIDATION_SCHEMA
            and redone.get("phase") == (RECOVERY_PHASE if recovery else REVALIDATION_PHASE)
            and redone.get("status") == "GO_PAPER_SCALPING"
            and redone.get("market_provider", "PPI") == provider
            and redone.get("candidate_sha") == candidate
            and (not recovery or (redone.get("candidate_tree_sha"), redone.get("image_id")) ==
                 (intent["candidate_tree_sha"], intent["image_id"]))
            and redone.get("trade_day_ar") == now.date().isoformat()
            and redone.get("daily_gate_sha256") == hashlib.sha256(daily_raw).hexdigest()
            and redone.get("operator_run_id") == intent["rearm_run_id"]
            and revalidated_at.date() == now.date()
            and _intraday_revalidation_window(now) and _intraday_revalidation_window(revalidated_at)
            and issued < revalidated_at <= now)


def iol_recovery_due(operator_directory, *, now):
    """The existing health tick only retries a recoverable same-session RED."""
    from scripts.porota_operator_intent import private_read

    now = now.astimezone(TZ)
    if not _intraday_revalidation_window(now):
        return False
    authorized, _, intent = evaluate_rearm(now=now, context="host")
    if not authorized:
        return False
    try:
        raw = private_read(operator_directory / HOST_V2_RECEIPT.name)
        daily = json.loads(raw)
        issued = _gate_time(daily, now)
        if (not _technical_t10_hold(daily, IOL_PROVIDER) or _daily_gate_error(
                daily, issued, now, provider=IOL_PROVIDER, recover_technical_hold=True)):
            return False
    except (OSError, TypeError, KeyError, ValueError):
        return False
    try:
        redone = json.loads(private_read(operator_directory / REVALIDATION_NAME))
        if type(redone) is not dict:
            return True
        return not _valid_revalidation(redone, raw, now=now, issued=issued, provider=IOL_PROVIDER,
                                       candidate=intent["candidate_sha"], intent=intent, recovery=True)
    except (OSError, TypeError, KeyError, ValueError):
        return True


def admission_status(*, now=None, candidate_sha=None, receipt_path=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    pause_reason = operator_pause_reason(now)
    if pause_reason:
        return False, pause_reason
    candidate_sha = candidate_sha if candidate_sha is not None else os.getenv("POROTA_BUILD_SHA", "")
    path = Path(receipt_path or (RUNTIME_V2_RECEIPT if
                now.date().isoformat() >= NO_GO_UNTIL_EXPLICIT_REARM_FROM_AR
                else RUNTIME_RECEIPT))
    if not isinstance(candidate_sha, str) or not SHA.fullmatch(candidate_sha):
        return False, "PAPER_PREOPEN_BUILD_IDENTITY_MISSING"
    try:
        root_owned = path == RUNTIME_V2_RECEIPT
        provider = os.getenv("POROTA_RC6_MARKET_PROVIDER", "PPI")
        receipt, raw = _read_gate(path, root_owned=root_owned)
        issued = _gate_time(receipt, now)
        recovery = root_owned and _technical_t10_hold(receipt, provider)
        reason = _daily_gate_error(receipt, issued, now, provider=provider, recover_technical_hold=recovery)
        if reason:
            return False, reason
        if not recovery and receipt["candidate_sha"] == candidate_sha:
            return True, ""
        if not root_owned:
            return False, "PAPER_PREOPEN_RELEASE_MISMATCH"
        try:
            redone, _ = _read_gate(path.with_name(REVALIDATION_NAME), root_owned=True)
        except FileNotFoundError:
            return False, "NO_GO_PAPER_SCALPING" if recovery else "PAPER_PREOPEN_RELEASE_MISMATCH"
        allowed, _, intent = evaluate_rearm(now=now)
        if allowed and _valid_revalidation(redone, raw, now=now, issued=issued, provider=provider,
                                          candidate=candidate_sha, intent=intent, recovery=recovery):
            return True, ""
        return False, "NO_GO_PAPER_SCALPING" if recovery else "PAPER_PREOPEN_RELEASE_REVALIDATION_INVALID"
    except FileNotFoundError:
        return False, "PAPER_PREOPEN_RECEIPT_UNSAFE"
    except ValueError as error:
        reason = str(error)
        if reason in {"PAPER_PREOPEN_RECEIPT_UNSAFE", "PAPER_PREOPEN_RECEIPT_CHANGED",
                      "PAPER_V2_PREOPEN_ROOT_DIRECTORY_UNTRUSTED"}:
            return False, reason
        return False, "PAPER_PREOPEN_RECEIPT_INVALID"
    except (OSError, KeyError, TypeError):
        return False, "PAPER_PREOPEN_RECEIPT_INVALID"


def _pending_identity_material_change(connection, identity):
    """Read only this identity's unresolved transitions; never waive/delete one.

    Use the existing material projection, so a provenance-only historical
    change is not promoted into a new trading veto. Missing/corrupt payloads
    remain unverified. Fee-authority waivers are not option/underlying waivers.
    """
    from cp_contract_evidence_v2_hf6 import material_evidence_hash
    rows = connection.execute("""
        SELECT prev.evidence_json,curr.evidence_json
        FROM contract_evidence_v2_changes ch
        LEFT JOIN contract_evidence_v2_snapshots prev
          ON prev.family=ch.family AND prev.ticker=ch.ticker AND prev.market=ch.market
         AND prev.currency=ch.currency AND prev.settlement=ch.settlement
         AND prev.source_class=ch.source_class AND prev.evidence_hash=ch.previous_hash
        LEFT JOIN contract_evidence_v2_snapshots curr
          ON curr.family=ch.family AND curr.ticker=ch.ticker AND curr.market=ch.market
         AND curr.currency=ch.currency AND curr.settlement=ch.settlement
         AND curr.source_class=ch.source_class AND curr.evidence_hash=ch.current_hash
        WHERE ch.family=? AND ch.ticker=? AND ch.market=? AND ch.currency=?
          AND ch.settlement=? AND ch.status='CHANGED_REVIEW_REQUIRED'
    """, identity).fetchall()
    for previous, current in rows:
        if previous is None or current is None:
            return True
        previous, current = json.loads(previous), json.loads(current)
        if (not isinstance(previous, dict) or not isinstance(current, dict)
                or material_evidence_hash(previous) != material_evidence_hash(current)):
            return True
    return False


def _current_option_dependency_contract(connection, record, now):
    """Normalize only an OPTION/dependency's current snapshots, without init/write."""
    from bs_instrument_contracts import contract_from_metadata
    from rc6_contract_bridge import normalize_group
    identity = tuple(record[key] for key in ("instrument_type", "ticker", "market", "currency", "settlement"))
    pending = _pending_identity_material_change(connection, identity)
    rows = connection.execute("""
        SELECT c.*,s.source_ref,s.effective_at,s.evidence_json,s.snapshot_id AS verified_snapshot_id
        FROM contract_evidence_v2_current c LEFT JOIN contract_evidence_v2_snapshots s
          ON s.snapshot_id=c.snapshot_id AND s.family=c.family AND s.ticker=c.ticker
         AND s.market=c.market AND s.currency=c.currency AND s.settlement=c.settlement
         AND s.source_class=c.source_class AND s.evidence_hash=c.evidence_hash
        WHERE c.family=? AND c.ticker=? AND c.market=? AND c.currency=? AND c.settlement=?
    """, identity).fetchall()
    records = []
    for row in rows:
        item = dict(row)
        if item.pop("verified_snapshot_id") is None:
            raise ValueError("OPTION_CURRENT_SNAPSHOT_LINK_UNVERIFIED")
        item["evidence"] = json.loads(item.pop("evidence_json"))
        item["change_pending"] = pending
        records.append(item)
    normalized = normalize_group(records, now=now)
    if normalized["contract_bridge"]["status"] != "NORMALIZED" or not normalized["financial_contract_v17"]:
        raise ValueError("OPTION_CURRENT_SNAPSHOT_UNVERIFIED")
    return contract_from_metadata(record["ticker"], record["instrument_type"], normalized["financial_contract_v17"])


def iol_market_quote_reason(quote, now):
    """Require native IOL proof for the exact trade and executable book."""
    from bs_instrument_contracts import aware_datetime, decimal_value
    from co_market_sessions_hf6 import byma_paper_spot_phase

    if quote.metadata_source != "IOL_REST" or quote.opening_block_reason:
        return quote.opening_block_reason or "IOL_EXECUTABLE_SOURCE_UNVERIFIED"
    temporal = quote.time_error(now, require_trade=True)
    if temporal:
        return temporal
    evidence = quote.iol_trade_evidence
    currency, market = quote.monetary_identity()
    key = (quote.symbol, quote.asset_class, market, currency, quote.settlement)
    if not isinstance(evidence, dict) or evidence.get("identity") != list(key):
        return "IOL_LAST_TRADE_EVIDENCE_MISSING"
    generation = evidence.get("gateway_generation")
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{64}", generation):
        return "IOL_LAST_TRADE_EVIDENCE_UNVERIFIED"
    trade_at = aware_datetime(quote.trade_at)
    book_at = aware_datetime(quote.book_at)
    if (aware_datetime(evidence.get("trade_at")) != trade_at or
            not trade_at <= aware_datetime(evidence.get("received_at")) <=
            aware_datetime(evidence.get("quote_received_at")) <= aware_datetime(quote.observed_at) or
            decimal_value(evidence.get("price"), "IOL last trade", positive=True) != quote.last):
        return "IOL_LAST_TRADE_EVIDENCE_MISMATCH"
    if any(at.astimezone(TZ).date() != now.astimezone(TZ).date() or byma_paper_spot_phase(at) != "OPEN"
           for at in (trade_at, book_at, now)):
        return "IOL_MARKET_EVIDENCE_WRONG_SESSION"
    if not (0 < quote.bid <= quote.ask and quote.bid_size > 0 and quote.ask_size > 0):
        return "IOL_BOOK_TWO_SIDED_UNVERIFIED"
    return ""


def _option_admission_reason(store, record, contract, now):
    from rc6_ppi_option_contract_policy import (
        PPI_OPTION_UNDERLYING_SETTLEMENT, current_option_contract_reason)

    with store.connect() as connection:
        underlying_rows = [dict(row) for row in connection.execute("""
            SELECT * FROM financial_instrument_catalog
            WHERE ticker=? AND instrument_type IN ('ACCIONES','CEDEARS')
        """, (contract.underlying,)).fetchall()]
        reason = current_option_contract_reason(
            record, underlying_rows, contract, checked_at=now.isoformat())
        if reason:
            return reason
        current_spec = _current_option_dependency_contract(connection, record, now)
        for underlying in underlying_rows:
            if (underlying.get("status") == "AVAILABLE"
                    and underlying.get("market") == "BYMA"
                    and underlying.get("currency") == "ARS"
                    and underlying.get("settlement") == PPI_OPTION_UNDERLYING_SETTLEMENT
                    and str(json.loads(underlying.get("metadata_json") or "{}").get(
                        "_discovery_source") or "").upper() == "PPI_PRIMARY"):
                _current_option_dependency_contract(connection, underlying, now)
        return current_option_contract_reason(
            record, underlying_rows, current_spec, checked_at=now.isoformat())


def _iol_spot_admission_reason(quote, store, now):
    from bu_instrument_catalog import iol_spot_catalog, quote_terms
    from rc6_dynamic_universe.routing import EQUITY_FAMILIES
    from rc6_fixed_income_paper_strategy import FAMILIES as FIXED_INCOME_FAMILIES

    if quote.asset_class not in EQUITY_FAMILIES | FIXED_INCOME_FAMILIES | {"OPCIONES"}:
        return "IOL_SPECIALIZED_ENTRY_EVIDENCE_REQUIRED"
    if reason := iol_market_quote_reason(quote, now):
        return reason
    currency, market = quote.monetary_identity()
    key = (quote.symbol, quote.asset_class, market, currency, quote.settlement)
    record = iol_spot_catalog(store, now=now, identity=key, include_contracts=True).get(key)
    terms = quote_terms(record, now=now, iol_live_settlement=True)
    if terms.get("opening_block_reason"):
        return terms["opening_block_reason"]
    if quote.contract is None or quote.contract != terms.get("contract"):
        return "PAPER_EXACT_CONTRACT_MISMATCH"
    if quote.asset_class == "OPCIONES":
        return _option_admission_reason(store, record, quote.contract, now)
    if quote.asset_class in FIXED_INCOME_FAMILIES:
        from rc6_fixed_income_iol_evidence import iol_contract_reason
        return iol_contract_reason(key, quote.contract)
    return ""


def paper_admission_guard(quote, store):
    """Recheck global GO and exact candidate terms at the final PAPER admission."""
    allowed, reason = admission_status()
    if not allowed:
        return False, reason
    try:
        if os.getenv("POROTA_RC6_MARKET_PROVIDER") == "IOL_REST":
            reason = _iol_spot_admission_reason(quote, store, datetime.now(TZ))
            return not reason, reason
        from bu_instrument_catalog import contract_for, lookup
        currency, market = quote.monetary_identity()
        record = lookup(store, quote.symbol, quote.asset_class, quote.settlement,
                        market=market, currency=currency)
        expected = {"FUTUROS": "READY_PAPER_FUTURES",
                    "OPCIONES": "READY_PAPER_OPTION_LONG"}.get(quote.asset_class, "READY_PAPER_SPOT")
        if (not record or record.get("status") != "AVAILABLE"
                or record.get("capability") != expected or record.get("_admission_reason")):
            return False, "PAPER_EXACT_CONTRACT_NOT_READY"
        spec = contract_for(record)
        if (spec.settlement != quote.settlement or spec.currency != currency
                or spec.market != market or (quote.asset_class in {"OPCIONES", "FUTUROS"}
                                      and quote.contract is None)):
            return False, "PAPER_EXACT_CONTRACT_MISMATCH"
        if quote.asset_class == "OPCIONES":
            reason = _option_admission_reason(store, record, quote.contract, datetime.now(TZ))
            if reason:
                return False, reason
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
        return False, "PAPER_EXACT_CONTRACT_UNAVAILABLE"
    return True, ""
