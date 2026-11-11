"""Durable, release-bound preopen interlock for live RC6 PAPER admissions.

The preopen service publishes one small receipt after its read-only probes.
Consumers fail closed on missing, stale or mismatched receipts. This module
contains no order client and never authorizes a real broker route.
"""
from __future__ import annotations

import json
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
)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
HOST_ROOT = Path("/opt/porota-trading")
HOST_RECEIPT = HOST_ROOT / "data/deploy/paper_scalping_preopen_gate_v1.json"
RUNTIME_RECEIPT = Path("/app/data/deploy/paper_scalping_preopen_gate_v1.json")
# The v2 preopen authority is also in a root-owned readonly mount. A botuser
# container may write DATA, so placing a new GO file in DATA would be forgeable.
HOST_V2_RECEIPT = OPERATOR_DIR_HOST / "paper-preopen-v2.json"
RUNTIME_V2_RECEIPT = OPERATOR_DIR_RUNTIME / "paper-preopen-v2.json"
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
    state_path = Path(state_path or HOST_ROOT / "data/deploy/CURRENT_STATE_V2.json")
    receipt_path = Path(receipt_path or (HOST_V2_RECEIPT if
                        now.date().isoformat() >= NO_GO_UNTIL_EXPLICIT_REARM_FROM_AR
                        else HOST_RECEIPT))
    candidate = ""
    try:
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
                          "PREOPEN_GREEN_RELEASE_PAPER_ZERO" if allowed else "PREOPEN_OR_RELEASE_NOT_GREEN")}
    # A125's 27-Oct LIVE gate is an immutable link in the A138/A143 scheduler
    # recovery chain. Neither a manual preopen nor a subsequent day's T-10
    # may overwrite it while that chain is active. The separate A151 sidecar
    # attests today's NO_GO; any future rearm requires a reviewed migration.
    if (receipt_path == HOST_RECEIPT and
            now.date().isoformat() >= "2026-10-27"):
        raise ValueError("HISTORICAL_A125_LIVE_GATE_IMMUTABLE")
    if receipt_path.parent.is_symlink() or not receipt_path.parent.is_dir() or receipt_path.is_symlink():
        raise ValueError("UNSAFE_GATE_PATH")
    if receipt_path == HOST_V2_RECEIPT:
        observed = os.lstat(receipt_path.parent)
        if (os.geteuid() != 0 or observed.st_uid != 0 or
                observed.st_mode & 0o022):
            raise ValueError("PAPER_V2_ROOT_GATE_PATH_REQUIRED")
    fd, temporary = tempfile.mkstemp(prefix=".paper-gate-", dir=receipt_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o644)
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
        if path == RUNTIME_V2_RECEIPT:
            parent = os.lstat(path.parent)
            if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or
                    parent.st_mode & 0o022):
                return False, "PAPER_V2_PREOPEN_ROOT_DIRECTORY_UNTRUSTED"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            entry = os.fstat(fd)
            if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1 or
                    not 0 < entry.st_size <= 4096 or
                    (path == RUNTIME_V2_RECEIPT and
                     (entry.st_uid != 0 or entry.st_mode & 0o022))):
                return False, "PAPER_PREOPEN_RECEIPT_UNSAFE"
            with os.fdopen(fd, "rb", closefd=False) as stream:
                encoded = stream.read(4097)
            if len(encoded) != entry.st_size:
                return False, "PAPER_PREOPEN_RECEIPT_CHANGED"
        finally:
            os.close(fd)
        receipt = json.loads(encoded)
        issued = datetime.fromisoformat(receipt["issued_at_ar"])
        if issued.tzinfo is None or issued.utcoffset() != now.utcoffset():
            raise ValueError("PREOPEN_CLOCK_INVALID")
        issued = issued.astimezone(TZ)
        expected_schema = ("RC6_PAPER_PREOPEN_GATE_V2" if
                           now.date().isoformat() >= NO_GO_UNTIL_EXPLICIT_REARM_FROM_AR
                           else "RC6_PAPER_PREOPEN_GATE_V1")
        good = (receipt.get("schema") == expected_schema
                and receipt.get("status") == "GO_PAPER_SCALPING"
                and receipt.get("phase") == "T_MINUS_10"
                and receipt.get("candidate_sha") == candidate_sha
                and receipt.get("trade_day_ar") == now.date().isoformat()
                and issued.date() == now.date()
                and 10 * 60 + 20 <= issued.hour * 60 + issued.minute < 10 * 60 + 30
                and 0 <= (now - issued).total_seconds() <= 7 * 3600)
        return (True, "") if good else (False, "NO_GO_PAPER_SCALPING")
    except FileNotFoundError:
        return False, "PAPER_PREOPEN_RECEIPT_UNSAFE"
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
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


def paper_admission_guard(quote, store):
    """Recheck global GO and exact candidate terms at the final PAPER admission."""
    allowed, reason = admission_status()
    if not allowed:
        return False, reason
    try:
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
            from rc6_ppi_option_contract_policy import current_option_contract_reason
            now = datetime.now(TZ)
            with store.connect() as connection:
                underlying_rows = [dict(row) for row in connection.execute("""
                    SELECT * FROM financial_instrument_catalog
                    WHERE ticker=? AND instrument_type IN ('ACCIONES','CEDEARS')
                """, (spec.underlying,)).fetchall()]
                reason = current_option_contract_reason(
                    record, underlying_rows, quote.contract, checked_at=now.isoformat())
                if reason:
                    return False, reason
                current_spec = _current_option_dependency_contract(connection, record, now)
                for underlying in underlying_rows:
                    if (underlying.get("status") == "AVAILABLE"
                            and underlying.get("market") == "BYMA"
                            and underlying.get("currency") == "ARS"
                            and underlying.get("settlement") == "INMEDIATA"
                            and str(json.loads(underlying.get("metadata_json") or "{}").get(
                                "_discovery_source") or "").upper() == "PPI_PRIMARY"):
                        _current_option_dependency_contract(connection, underlying, now)
                reason = current_option_contract_reason(
                    record, underlying_rows, current_spec, checked_at=now.isoformat())
                if reason:
                    return False, reason
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
        return False, "PAPER_EXACT_CONTRACT_UNAVAILABLE"
    return True, ""
