"""Bounded READ_ONLY export of an explicitly named twenty-session cohort.

Only the existing spot PAPER ledger is supported. This is an offline tool, not
an ingestion worker. It has no default source path, credentials or network use.
"""
import hashlib
import hmac
import ctypes
import errno
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as daytime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from .sqlite_snapshot import SnapshotError, readonly_copy

ART = ZoneInfo("America/Argentina/Buenos_Aires")
SCHEMA = "rc6.sanitized-20session-evidence.v1"
SCHEMA_V2 = "rc6.sanitized-20session-evidence.v2"
CALCULATION = "rc6-independent-fill-cashflow.v1"
FILES = {"positions.jsonl", "fills.jsonl", "methodology.json", "manifest.json"}
FILES_V2 = FILES | {"voids.jsonl"}
ADMIN_VOID_REASON = "ADMIN_CANCEL_NO_MARKET_EXECUTION"
A207_ADMIN_AUTHORITY = "ISSUE773_A207_PAPER_ACCOUNTING_REVERSAL_20261030"
ADMIN_VOID_AUTHORITIES = frozenset({
    "ISSUE624_OPERATOR_REQUEST_20261019",
    "ISSUE773_PAPER_ADMIN_CLOSE_OPERATOR_20261030",
    A207_ADMIN_AUTHORITY,
})
FAMILIES = {"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"}
EXIT_CODES = {"EOD_PAPER", "STOP_PAPER", "TAKE_PROFIT_PAPER", "MAX_HOLD_PAPER",
              "SCALPING_MAX_HOLD_PAPER", "OVERNIGHT_CARRY_EXIT", "DAILY_RISK_HARD_STOP",
              "DAILY_RISK_SOFT_STOP", "EXPIRY"}


class EvidenceError(ValueError):
    """A fixed reason code; never include source rows, paths or SQL errors."""


@dataclass(frozen=True)
class Limits:
    positions: int = 2000
    fills: int = 10000
    bytes: int = 32 * 1024 * 1024
    row_bytes: int = 65536
    seconds: float = 10.0

    def __post_init__(self):
        if (type(self.positions) is not int or not 1 <= self.positions <= 10000
                or type(self.fills) is not int or not 1 <= self.fills <= 100000
                or type(self.bytes) is not int or not 1024 <= self.bytes <= 128 * 1024 * 1024
                or type(self.row_bytes) is not int or not 512 <= self.row_bytes <= 65536
                or isinstance(self.seconds, bool) or not isinstance(self.seconds, (int, float))
                or not 0 < self.seconds <= 60):
            raise EvidenceError("BOUNDED_BUDGET_REQUIRED")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def decimal(value, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or len(str(value)) > 100:
        raise EvidenceError("INVALID_DECIMAL")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise EvidenceError("INVALID_DECIMAL") from None
    if (not result.is_finite() or result.adjusted() > 24 or result.as_tuple().exponent < -18
            or (positive and result <= 0) or (nonnegative and result < 0)):
        raise EvidenceError("INVALID_DECIMAL")
    return result


def money(value):
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def stamp(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 40:
        raise EvidenceError("SOURCE_CLOCK_REQUIRED")
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EvidenceError("SOURCE_CLOCK_REQUIRED") from None
    if at.tzinfo is None or at.utcoffset() is None:
        raise EvidenceError("SOURCE_CLOCK_REQUIRED")
    return at.astimezone(timezone.utc)


def timestamp(value):
    return stamp(value).isoformat(timespec="microseconds")


def token(value, *, maximum=100):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/+^ -]{1," + str(maximum) + "}", value):
        raise EvidenceError("INVALID_SANITIZED_FIELD")
    return value


def cohort_sessions(sessions):
    if not isinstance(sessions, (list, tuple)) or len(sessions) != 20 or len(set(sessions)) != 20:
        raise EvidenceError("EXACT_20_SESSIONS_REQUIRED")
    try:
        parsed = [date.fromisoformat(s) for s in sessions]
    except (TypeError, ValueError, RecursionError):
        raise EvidenceError("EXACT_20_SESSIONS_REQUIRED") from None
    if any(d.isoformat() != s for s, d in zip(sessions, parsed)):
        raise EvidenceError("EXACT_20_SESSIONS_REQUIRED")
    return sorted(sessions)


def bounds(sessions):
    start = datetime.combine(date.fromisoformat(sessions[0]), daytime.min, ART)
    cutoff = datetime.combine(date.fromisoformat(sessions[-1]) + timedelta(days=1), daytime.min, ART)
    return start.astimezone(timezone.utc), cutoff.astimezone(timezone.utc)


class Budget:
    def __init__(self, limits):
        self.limits = limits
        self.deadline = time.monotonic() + limits.seconds
        self.consumed = 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise EvidenceError("TIME_BUDGET_EXHAUSTED")

    def consume(self, value):
        self.check()
        data = canonical(value).encode()
        if len(data) > self.limits.row_bytes:
            raise EvidenceError("ROW_BYTE_BUDGET_EXHAUSTED")
        self.consumed += len(data) + 1
        if self.consumed > self.limits.bytes:
            raise EvidenceError("BYTE_BUDGET_EXHAUSTED")


def pseudonym(key, namespace, value):
    return namespace + "_" + hmac.new(key, (namespace + "\0" + canonical(value)).encode(), hashlib.sha256).hexdigest()


def _safe_file(path):
    path = Path(path).absolute()
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise EvidenceError("REGULAR_UNALIASED_FILE_REQUIRED")
        if path.resolve() != path or any(p.is_symlink() for p in path.parents):
            raise EvidenceError("REGULAR_UNALIASED_FILE_REQUIRED")
    except OSError:
        raise EvidenceError("SOURCE_UNAVAILABLE") from None
    return path


@contextmanager
def readonly_snapshot(path, budget):
    """A verified copy, mode=ro, query_only and SELECT-only authorizer.

    No SQLite connection touches source main/WAL/SHM. The 512 MiB source-copy
    cap is independent of the much smaller sanitized output budget.
    """
    path = _safe_file(path)
    try:
        with readonly_copy(path, deadline=budget.deadline) as connection:
            tables = {r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('observer_state','paper_positions','paper_fills')")}
            if tables != {"observer_state", "paper_positions", "paper_fills"}:
                raise EvidenceError("SOURCE_SCHEMA_UNAVAILABLE")

            # The v2 void proof reads only the bounded source-side receipt,
            # original buy and economic sidecar tables. Their raw values are
            # never exported; only domain-separated HMAC commitments survive.
            allowed_tables = tables | {
                'sqlite_master', 'paper_admin_cancellations', 'paper_exit_intents',
                'paper_events', 'paper_spot_sales', 'paper_sale_receivables',
                'paper_learning_samples', 'paper_fi_lots',
                'paper_fi_economic_events', 'paper_fi_cash_entries',
                'paper_admin_reversal_batches',
            }
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                       sqlite3.SQLITE_TRANSACTION}

            def authorize(action, first, second, database, trigger):
                if action not in allowed or trigger is not None:
                    return sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_READ and first not in allowed_tables:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            connection.execute("BEGIN")
            states = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1 LIMIT 2").fetchall()
            if (len(states) != 1 or states[0]["mode"] != "PRODUCTION_PAPER"
                    or type(states[0]["real_orders_sent"]) is not int or states[0]["real_orders_sent"] != 0):
                raise EvidenceError("PAPER_SOURCE_SAFETY_REQUIRED")
            budget.check()
            yield connection
            budget.check()
    except SnapshotError as exc:
        raise EvidenceError(str(exc)) from None
    except sqlite3.Error:
        if time.monotonic() >= budget.deadline:
            raise EvidenceError("TIME_BUDGET_EXHAUSTED") from None
        raise EvidenceError("SOURCE_READ_FAILED") from None


def _projection(column, maximum):
    # SQLite evaluates length before returning a column; a huge features object
    # or malicious text never becomes a Python object or an output artifact.
    return f'CASE WHEN length(CAST("{column}" AS BLOB))<={maximum} THEN "{column}" ELSE NULL END AS "{column}"'


POSITION_COLUMNS = ("paper_id", "strategy_version", "symbol", "asset_class", "currency", "market",
                    "settlement", "status", "quantity", "entry_cost", "exit_cost", "gross_pnl", "net_pnl",
                    "opened_at", "closed_at", "close_reason", "features_json")
FILL_COLUMNS = ("id", "paper_id", "side", "filled_at", "quantity", "price", "costs", "slippage")
METHOD = {
    "schema": "rc6.20session-methodology.v1", "calculation_version": CALCULATION,
    "timezone": "America/Argentina/Buenos_Aires",
    "selection": "All spot positions overlapping the cohort interval; complete opening/exit fills through cutoff exclusive. Closed-cohort metrics include only closes on the supplied twenty sessions.",
    "arithmetic": "Decimal precision 160 for bounded fill quantities * execution prices * explicit cash multiplier. Closed gross = all sale cash - all purchase cash; costs = explicit fill charges; net = gross - costs. No FX conversion or cross-currency total.",
    "partials": "Every fill remains separate. Sell before buy, over-sale, duplicate IDs and missing opening/closing quantities fail. Open survivors have no invented realized/unrealized P&L.",
    "slippage": "Already embedded in execution prices; quoted slippage is retained as metadata and never charged twice.",
    "fees": "The current ledger stores aggregate fill costs. Commission/rights/VAT/account-specific fees remain NO_VERIFICADO; no reverse engineering of a tariff.",
    "source_clocks": "Native opening, closing and fill times plus whitelisted performance clocks only; no capture-time substitution.",
    "privacy": "Domain-separated HMAC-SHA256 position/fill IDs and source-row commitments. External high-entropy seed never leaves the caller. No raw ID, account field, source filename, features object, key or reversible mapping is exported.",
    "lineage": "Source HMAC commitments attest the approved projection to a seed holder; public SHA256 row/file commitments attest exported bytes. Missing Git/config/contract/provider clocks are explicitly NO_VERIFICADO.",
    "scope": "SPOT_PAPER_LEDGER_ONLY; futures/caucion/FCI accounting, cash/equity, corporate actions and executable fills remain outside this dataset and require separate evidence.",
    "honesty": "Recomputation is not source authentication. An independently retained manifest digest and independent source owner reconciliation are required. Twenty session dates certify selection, not source availability, edge or runtime.",
}
METHOD_V2 = {
    **METHOD,
    "schema": "rc6.20session-methodology.v2",
    "selection": ("All source spot positions overlapping the cohort. Verified administrative voids "
                  "are retained in voids.jsonl, excluded from performance positions/fills, "
                  "and never treated as traded closes. A void after the cohort cutoff "
                  "remains an OPEN_AT_CUTOFF historical survivor."),
    "admin_voids": ("One intact BUY_SIMULATED, one exact administrator receipt, "
                    "one matching non-market event and EOD intent, no sale/receivable/"
                    "learning label/FI sidecar. A207 authority additionally requires one "
                    "SHA/tree/owner/exposures/targets-sealed batch journal with exact spot "
                    "membership; full target-set completeness remains NO_VERIFICADO. "
                    "Source rows are HMAC committed; independent "
                    "package checks do not authenticate the source without owner review."),
}


def _lineage(features):
    original = features.get("performance_lineage", {})
    if not isinstance(original, dict):
        raise EvidenceError("INVALID_SOURCE_LINEAGE")
    clocks = {}
    for name in ("signal_started_at", "signal_at", "decision_at", "intent_at", "entry_fill_committed_at"):
        value = original.get(name)
        clocks[name] = timestamp(value) if value is not None else None
    hashes = {}
    for name, length in (("git_sha", 40), ("candidate_tree_sha", 40), ("configuration_fingerprint", 64), ("manifest_sha256", 64)):
        value = original.get(name)
        hashes[name] = value if isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) else None
    return {"clocks": clocks, "hashes": hashes,
            "status": "SOURCE_FIELDS_PRESENT" if all(hashes.values()) and all(clocks.values()) else "NO_VERIFICADO"}


def _position(row, key, sessions, start, cutoff, *, original_source=None):
    raw = dict(row)
    if any(raw.get(k) is None for k in ("paper_id", "symbol", "strategy_version", "asset_class", "currency", "market", "settlement", "status", "quantity", "opened_at", "features_json", "entry_cost")):
        raise EvidenceError("SOURCE_ROW_INCOMPLETE_OR_OVERSIZE")
    opened = stamp(raw["opened_at"])
    closed = stamp(raw["closed_at"]) if raw["closed_at"] is not None else None
    if closed is not None and closed < opened:
        raise EvidenceError("POSITION_CLOCK_ORDER")
    if raw["status"] not in {"OPEN", "CLOSED"} or (raw["status"] == "CLOSED") != (closed is not None):
        raise EvidenceError("POSITION_LIFECYCLE_MISMATCH")
    if opened >= cutoff or (closed is not None and closed < start):
        return None
    try:
        features = json.loads(raw["features_json"])
    except (TypeError, ValueError):
        raise EvidenceError("INVALID_SOURCE_FEATURES") from None
    if not isinstance(features, dict):
        raise EvidenceError("INVALID_SOURCE_FEATURES")
    family = token(raw["asset_class"]).upper()
    if family not in FAMILIES:
        raise EvidenceError("SPECIALIZED_LEDGER_REQUIRED")
    multiplier = features.get("contract_cash_multiplier")
    if multiplier is None and family in {"ACCIONES", "CEDEARS", "ETFS"}:
        multiplier = "1"
        multiplier_status = "SPOT_SHARE_CASH_CONVENTION"
    else:
        multiplier_status = "EXPLICIT_SOURCE"
    if multiplier is None:
        raise EvidenceError("CONTRACT_MULTIPLIER_REQUIRED")
    factor = decimal(multiplier, positive=True)
    lineage = _lineage(features)
    native = features.get("performance_lineage", {})
    strategy = native.get("strategy_id")
    at_cutoff_closed = closed is not None and closed < cutoff
    close_session = closed.astimezone(ART).date().isoformat() if at_cutoff_closed else None
    if at_cutoff_closed and close_session not in sessions:
        raise EvidenceError("COHORT_SESSION_MISSING")
    reported = {k: (money(decimal(raw[k], nonnegative=k.endswith("cost"))) if raw[k] is not None else None)
                for k in ("entry_cost", "exit_cost", "gross_pnl", "net_pnl")}
    if at_cutoff_closed and any(reported[k] is None for k in reported):
        raise EvidenceError("CLOSED_LEDGER_AMOUNT_REQUIRED")
    if not at_cutoff_closed:
        reported = {"entry_cost": reported["entry_cost"], "exit_cost": None, "gross_pnl": None, "net_pnl": None}
    code = raw["close_reason"] if at_cutoff_closed else None
    reason = code if code in EXIT_CODES else ("LEGACY_REASON_UNMAPPED" if at_cutoff_closed else None)
    item = {
        "position_id": pseudonym(key, "pos", raw["paper_id"]),
        "strategy": token(strategy) if strategy else "NO_VERIFICADO",
        "strategy_version": token(raw["strategy_version"]),
        "symbol": token(raw["symbol"], maximum=40), "family": family,
        "market": token(raw["market"], maximum=40), "currency": token(raw["currency"], maximum=20),
        "settlement": token(raw["settlement"], maximum=40),
        "entry_at": opened.isoformat(timespec="microseconds"),
        "exit_at": closed.isoformat(timespec="microseconds") if at_cutoff_closed else None,
        "entry_session": opened.astimezone(ART).date().isoformat(), "exit_session": close_session,
        "lifecycle": "CLOSED" if at_cutoff_closed else "OPEN_AT_CUTOFF",
        "position_quantity": money(decimal(raw["quantity"], positive=True)),
        "cash_multiplier": money(factor), "multiplier_status": multiplier_status,
        "units": "SHARES" if family in {"ACCIONES", "CEDEARS", "ETFS"} else "CONTRACT_QUANTITY",
        "units_per_lot": None, "units_per_lot_status": "NO_VERIFICADO",
        "exit_reason": reason, "exit_reason_status": "NO_VERIFICADO" if reason == "LEGACY_REASON_UNMAPPED" else "NATIVE_CODE" if reason else "OPEN",
        "ledger": reported, "lineage": lineage,
        "source_row_commitment": pseudonym(key, "srcpos", original_source if original_source is not None else raw),
    }
    item["row_sha256"] = sha256(canonical(item).encode())
    return item


def _one_source_row(connection, query, params, error):
    # A unique source row is required; SQL limits ensure a corrupt duplicated
    # receipt cannot cause unbounded Python materialization.
    rows = connection.execute(query + " LIMIT 2", params).fetchall()
    if len(rows) != 1:
        raise EvidenceError(error)
    return dict(rows[0])


def _sealed_a207_membership(connection, paper_id, cancelled, key):
    """Validate one bounded A207 batch journal without claiming nonspot proof.

    The source DB is still unauthenticated until the owner reconciles it.
    The exporter has no caucion/future evidence contract and must not claim
    those family rows were independently revalidated here.
    """
    if not connection.execute('''SELECT 1 FROM sqlite_master WHERE type='table'
                              AND name='paper_admin_reversal_batches' ''').fetchone():
        raise EvidenceError("A207_ADMIN_BATCH_JOURNAL_MISSING")
    candidates = connection.execute('''SELECT candidate_sha,tree_sha,owner_comment_id,
        exposures_sha256,targets_json,completed_at,audit_digest_sha256
        FROM paper_admin_reversal_batches ORDER BY candidate_sha LIMIT 129''').fetchall()
    if not candidates or len(candidates) > 128:
        raise EvidenceError("A207_ADMIN_BATCH_JOURNAL_BOUND")
    matches = []
    for source in candidates:
        batch = dict(source)
        raw_targets = batch['targets_json']
        if not isinstance(raw_targets, str) or len(raw_targets.encode()) > 16384:
            raise EvidenceError("A207_ADMIN_BATCH_TARGETS_INVALID")
        try:
            targets = json.loads(raw_targets)
        except (ValueError, TypeError):
            raise EvidenceError("A207_ADMIN_BATCH_TARGETS_INVALID") from None
        if (type(targets) is not dict or set(targets) != {'spot','caucion','future'}
                or any(type(ids) is not list or len(ids) > 32
                       or any(type(identity) is not str or not 0 < len(identity) <= 128
                              for identity in ids)
                       or len(ids) != len(set(ids))
                       for ids in targets.values())
                or not any(targets.values())
                or raw_targets != canonical(targets)):
            raise EvidenceError("A207_ADMIN_BATCH_TARGETS_INVALID")
        fields = (batch['candidate_sha'], batch['tree_sha'], batch['owner_comment_id'],
                  batch['exposures_sha256'])
        if (not all(isinstance(value, str) for value in fields)
                or not re.fullmatch(r'[0-9a-f]{40}', fields[0])
                or not re.fullmatch(r'[0-9a-f]{40}', fields[1])
                or not re.fullmatch(r'[1-9][0-9]*', fields[2])
                or not re.fullmatch(r'[0-9a-f]{64}', fields[3])
                or not isinstance(batch['audit_digest_sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', batch['audit_digest_sha256'])):
            raise EvidenceError("A207_ADMIN_BATCH_AUTHORITY_INVALID")
        digest = hashlib.sha256('|'.join((*fields, raw_targets)).encode()).hexdigest()
        if digest != batch['audit_digest_sha256']:
            raise EvidenceError("A207_ADMIN_BATCH_SEAL_MISMATCH")
        if paper_id in targets['spot']:
            if stamp(batch['completed_at']) < cancelled:
                raise EvidenceError("A207_ADMIN_BATCH_COMPLETED_BEFORE_VOID")
            matches.append(batch)
    if len(matches) != 1:
        raise EvidenceError("A207_ADMIN_BATCH_SPOT_MEMBERSHIP_REQUIRED")
    return pseudonym(key, 'srcbatch', matches[0])


def _verified_admin_void(connection, row, key):
    """Verify an administrative void on the source snapshot, not from a label.

    A v2 void is evidence of a *withdrawn simulated entry*, never a close,
    mark, realized outcome or real broker transaction. Every source field read
    here is internal; published values contain pseudonyms and HMACs only.
    """
    raw = dict(row)
    required = {"paper_id", "status", "opened_at", "closed_at", "close_reason",
                "quantity", "entry_cost", "asset_class", "currency", "features_json"}
    if (not required <= set(raw) or raw["status"] != "CANCELLED_ADMIN"
            or raw["closed_at"] is None or raw["close_reason"] != ADMIN_VOID_REASON
            or any(raw[name] is not None for name in ("exit_cost", "gross_pnl", "net_pnl"))):
        raise EvidenceError("ADMIN_VOID_NOT_A_REALIZED_TRADE")
    opened, cancelled = stamp(raw["opened_at"]), stamp(raw["closed_at"])
    if cancelled < opened:
        raise EvidenceError("ADMIN_VOID_CLOCK_ORDER")
    try:
        features = json.loads(raw["features_json"])
    except (ValueError, TypeError):
        raise EvidenceError("ADMIN_VOID_SOURCE_FEATURES_INVALID") from None
    if not isinstance(features, dict):
        raise EvidenceError("ADMIN_VOID_SOURCE_FEATURES_INVALID")
    family, currency = token(raw["asset_class"]).upper(), token(raw["currency"]).upper()
    if family not in FAMILIES or currency not in {"ARS", "USD", "USD_MEP", "USD_CCL"}:
        raise EvidenceError("ADMIN_VOID_IDENTITY_UNVERIFIED")
    originals = _one_source_row(connection, '''SELECT source,entry_price,exit_price,quantity,
      entry_cost,opened_at,closed_at,status,close_reason FROM paper_positions
      WHERE paper_id=?''', (raw["paper_id"],), "ADMIN_VOID_POSITION_MISSING")
    if (originals["source"] != "PRODUCTION_PAPER" or originals["status"] != "CANCELLED_ADMIN"
            or originals["close_reason"] != ADMIN_VOID_REASON
            or originals["exit_price"] is not None
            or originals["opened_at"] != raw["opened_at"]
            or originals["closed_at"] != raw["closed_at"]
            or originals["quantity"] != raw["quantity"]
            or originals["entry_cost"] != raw["entry_cost"]):
        raise EvidenceError("ADMIN_VOID_SOURCE_POSITION_INVALID")
    quantity = decimal(originals["quantity"], positive=True)
    price = decimal(originals["entry_price"], positive=True)
    cost = decimal(originals["entry_cost"], nonnegative=True)
    receipt = _one_source_row(connection, '''SELECT cancelled_at,authority,
      original_quantity,original_entry_price,original_entry_cost,reason
      FROM paper_admin_cancellations WHERE paper_id=?''',
      (raw["paper_id"],), "ADMIN_VOID_RECEIPT_REQUIRED")
    if (receipt["authority"] not in ADMIN_VOID_AUTHORITIES
            or receipt["reason"] != ADMIN_VOID_REASON
            or stamp(receipt["cancelled_at"]) != cancelled
            or decimal(receipt["original_quantity"], positive=True) != quantity
            or decimal(receipt["original_entry_price"], positive=True) != price
            or decimal(receipt["original_entry_cost"], nonnegative=True) != cost):
        raise EvidenceError("ADMIN_VOID_RECEIPT_INVALID")
    original_buy = _one_source_row(connection, '''SELECT id,source,side,quantity,price,
      costs,filled_at FROM paper_fills WHERE paper_id=?''',
      (raw["paper_id"],), "ADMIN_VOID_ONLY_ONE_BUY_REQUIRED")
    if (original_buy["source"] != "PRODUCTION_PAPER"
            or original_buy["side"] != "BUY_SIMULATED"
            or stamp(original_buy["filled_at"]) != opened
            or decimal(original_buy["quantity"], positive=True) != quantity
            or decimal(original_buy["price"], positive=True) != price
            or decimal(original_buy["costs"], nonnegative=True) != cost):
        raise EvidenceError("ADMIN_VOID_ORIGINAL_BUY_INVALID")
    intent = _one_source_row(connection, '''SELECT state,cause FROM paper_exit_intents
      WHERE paper_id=?''', (raw["paper_id"],), "ADMIN_VOID_EXIT_INTENT_REQUIRED")
    if intent != {"state": "CANCELLED_ADMIN", "cause": "EOD_PAPER"}:
        raise EvidenceError("ADMIN_VOID_EXIT_INTENT_INVALID")
    event = _one_source_row(connection, '''SELECT event_at,source,event_type,
      CASE WHEN length(CAST(detail AS BLOB))<=1024 THEN detail ELSE NULL END detail
      FROM paper_events WHERE paper_id=?
      AND event_type='PAPER_ADMIN_CANCELLED_NO_MARKET' ''',
      (raw["paper_id"],), "ADMIN_VOID_EXACT_EVENT_REQUIRED")
    if (event["source"] != "PRODUCTION_PAPER"
            or event["event_type"] != "PAPER_ADMIN_CANCELLED_NO_MARKET"
            or stamp(event["event_at"]) != cancelled
            or not isinstance(event["detail"], str)
            or receipt["authority"] not in event["detail"]
            or "no SELL_SIMULATED" not in event["detail"]):
        raise EvidenceError("ADMIN_VOID_EVENT_INVALID")
    if (connection.execute('SELECT 1 FROM paper_spot_sales WHERE paper_id=? LIMIT 1',
                           (raw["paper_id"],)).fetchone()
            or connection.execute('SELECT 1 FROM paper_sale_receivables WHERE paper_id=? LIMIT 1',
                                  (raw["paper_id"],)).fetchone()
            or connection.execute('''SELECT 1 FROM paper_learning_samples
                WHERE paper_id=? AND label_timestamp IS NOT NULL LIMIT 1''',
                (raw["paper_id"],)).fetchone()):
        raise EvidenceError("ADMIN_VOID_SETTLEMENT_OR_LABEL_PRESENT")
    # Fixed-income entitlements outlive the spot row. Voiding the BUY while a
    # lot/event/cash sidecar remains would invalidate the FI cash reducer or
    # credit a payout on top of refunded PAPER principal. Fail closed.
    for sidecar in ("paper_fi_lots", "paper_fi_economic_events", "paper_fi_cash_entries"):
        if family in {"BONOS", "LETRAS", "OBLIGACIONES"}:
            if not connection.execute('SELECT 1 FROM sqlite_master WHERE name=? AND type=\'table\'',
                                      (sidecar,)).fetchone():
                raise EvidenceError("ADMIN_VOID_FI_SCHEMA_UNVERIFIED")
        if connection.execute('SELECT 1 FROM sqlite_master WHERE name=? AND type=\'table\'',
                              (sidecar,)).fetchone() and connection.execute(
                'SELECT 1 FROM ' + sidecar + ' WHERE paper_id=? LIMIT 1',
                (raw["paper_id"],)).fetchone():
            raise EvidenceError("ADMIN_VOID_FI_ECONOMIC_ENTITLEMENT_PRESENT")
    a207 = receipt['authority'] == A207_ADMIN_AUTHORITY
    batch_commitment = (_sealed_a207_membership(connection, raw['paper_id'], cancelled, key)
                        if a207 else None)
    result = {
        "position_id": pseudonym(key, "pos", raw["paper_id"]),
        "family": family, "currency": currency,
        "opened_at": opened.isoformat(timespec="microseconds"),
        "cancelled_at": cancelled.isoformat(timespec="microseconds"),
        "entry_session": opened.astimezone(ART).date().isoformat(),
        "admin_cancel_day": cancelled.astimezone(ART).date().isoformat(),
        "lifecycle": "CANCELLED_ADMIN_NO_EXECUTION",
        "authority": receipt["authority"], "close_reason": ADMIN_VOID_REASON,
        "source_proof": ("A207_SEALED_BATCH_SPOT_MEMBERSHIP_ONLY_FULL_BATCH_UNVERIFIED"
                         if a207 else "BOUNDED_READ_ONLY_SOURCE_RECEIPT_VERIFIED"),
        "a207_batch_commitment": batch_commitment,
        "a207_full_batch_complete": "NO_VERIFICADO" if a207 else "NOT_APPLICABLE",
        "sell_simulated_fills": 0, "realized_pnl": None,
        "source_row_commitment": pseudonym(key, "srcvoid", raw),
        "source_receipt_commitment": pseudonym(key, "srcadmin", receipt),
        "source_buy_commitment": pseudonym(key, "srcbuy", original_buy),
        "source_event_commitment": pseudonym(key, "srcevent", event),
    }
    result["row_sha256"] = sha256(canonical(result).encode())
    return result, cancelled


def _fill(row, key, identities, cutoff, index):
    raw = dict(row)
    if any(raw.get(k) is None for k in FILL_COLUMNS):
        raise EvidenceError("SOURCE_ROW_INCOMPLETE_OR_OVERSIZE")
    if raw["paper_id"] not in identities:
        raise EvidenceError("ORPHAN_FILL")
    at = stamp(raw["filled_at"])
    if raw["side"] not in {"BUY_SIMULATED", "SELL_SIMULATED"}:
        raise EvidenceError("PAPER_FILL_REQUIRED")
    if at >= cutoff:
        return None
    costs = money(decimal(raw["costs"], nonnegative=True))
    item = {
        "fill_id": pseudonym(key, "fill", raw["id"]), "position_id": identities[raw["paper_id"]],
        "side": raw["side"], "filled_at": at.isoformat(timespec="microseconds"),
        "position_fill_index": index,
        "session": at.astimezone(ART).date().isoformat(),
        "quantity": money(decimal(raw["quantity"], positive=True)),
        "price": money(decimal(raw["price"], positive=True)), "costs": costs,
        "cost_components": {"aggregate_explicit_charge": costs, "commission": None, "rights": None, "vat": None},
        "cost_components_status": "NO_VERIFICADO",
        "slippage_price_delta": money(decimal(raw["slippage"], nonnegative=True)),
        "slippage_accounting": "EMBEDDED_IN_EXECUTION_PRICE",
        "provider_at": None, "provider_clock_status": "NO_VERIFICADO",
        "source_row_commitment": pseudonym(key, "srcfill", raw),
    }
    item["row_sha256"] = sha256(canonical(item).encode())
    return item


def _read(source, key, sessions, budget):
    start, cutoff = bounds(sessions)
    positions, fills, voids, identities, raw_fill_ids = [], [], [], {}, set()
    with readonly_snapshot(source, budget) as connection:
        # Include invalid clocks in the bounded candidate set, then reject them
        # explicitly rather than letting SQLite NULL comparisons hide rows.
        selection = """(julianday(opened_at) IS NULL OR (julianday(opened_at)<=julianday(?)
                       AND (closed_at IS NULL OR julianday(closed_at) IS NULL OR julianday(closed_at)>=julianday(?))))"""
        projection = ",".join(_projection(k, 65536 if k == "features_json" else 256) for k in POSITION_COLUMNS)
        query = "SELECT " + projection + " FROM paper_positions WHERE " + selection + " ORDER BY paper_id LIMIT ?"
        rows = connection.execute(query, (cutoff.isoformat(), start.isoformat(), budget.limits.positions + 1))
        inspected = 0
        for row in rows:
            budget.consume(dict(row))
            inspected += 1
            if inspected > budget.limits.positions:
                raise EvidenceError("POSITION_ROW_BUDGET_EXHAUSTED")
            if row["status"] == "CANCELLED_ADMIN":
                void, cancelled = _verified_admin_void(connection, row, key)
                budget.consume(void)
                if cancelled < cutoff:
                    voids.append(void)
                    continue
                # A cancellation committed after this cohort's cutoff cannot
                # erase its historically OPEN PAPER exposure. The HMAC covers
                # the original canceled source row, not the PIT projection.
                projected = dict(row, status="OPEN", closed_at=None,
                                 close_reason=None, exit_cost=None,
                                 gross_pnl=None, net_pnl=None)
                item = _position(projected, key, sessions, start, cutoff,
                                 original_source=dict(row))
            else:
                item = _position(row, key, sessions, start, cutoff)
            if item is not None:
                raw_id = row["paper_id"]
                if raw_id in identities:
                    raise EvidenceError("DUPLICATE_POSITION")
                identities[raw_id] = item["position_id"]
                positions.append(item)
        if not positions and not voids:
            raise EvidenceError("EMPTY_COHORT_EXTERNAL_EVIDENCE_PENDING")
        projection = ",".join(_projection(k, 256) for k in FILL_COLUMNS)
        query = "SELECT " + projection + " FROM paper_fills WHERE paper_id=? ORDER BY id LIMIT ?"
        count = 0
        for raw_id in sorted(identities):
            index = 0
            # All fills for an overlapping position are inspected, including
            # future fills (not exported). A silent capped suffix is forbidden.
            for row in connection.execute(query, (raw_id, budget.limits.fills + 1)):
                count += 1
                if count > budget.limits.fills:
                    raise EvidenceError("FILL_ROW_BUDGET_EXHAUSTED")
                budget.consume(dict(row))
                if row["id"] in raw_fill_ids:
                    raise EvidenceError("DUPLICATE_FILL")
                raw_fill_ids.add(row["id"])
                item = _fill(row, key, identities, cutoff, index + 1)
                if item is not None:
                    index += 1
                    fills.append(item)
    positions.sort(key=lambda p: p["position_id"])
    fills.sort(key=lambda f: (f["position_id"], f["position_fill_index"]))
    voids.sort(key=lambda v: v["position_id"])
    return positions, fills, voids


def _write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(staging, destination):
    """Atomic directory publication that cannot replace a raced destination."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise EvidenceError("ATOMIC_NO_REPLACE_UNAVAILABLE")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(staging), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise EvidenceError("NEW_DESTINATION_REQUIRED")
        raise OSError(error, "PRIVATE_PACKAGE_PUBLICATION_FAILED")


def export_package(source, destination, *, sessions, pseudonym_key, limits=None):
    """Publish one new complete private package or fail without a final path.

    The key must be high-entropy and supplied out of band. No key or raw source
    is written even on a failed verification; only sanitized staging exists.
    """
    limits = limits or Limits()
    sessions = cohort_sessions(sessions)
    if not isinstance(pseudonym_key, bytes) or not 32 <= len(pseudonym_key) <= 4096:
        raise EvidenceError("EXTERNAL_PSEUDONYM_SEED_REQUIRED")
    source = _safe_file(source)
    destination = Path(destination).absolute()
    parent = destination.parent
    if destination.exists() or destination.is_symlink():
        raise EvidenceError("NEW_DESTINATION_REQUIRED")
    if not parent.is_dir() or parent.resolve() != parent:
        raise EvidenceError("SAFE_DESTINATION_REQUIRED")
    if any((p / ".git").exists() for p in (parent, *parent.parents)):
        raise EvidenceError("PRIVATE_ARTIFACT_OUTSIDE_REPOSITORY_REQUIRED")
    budget = Budget(limits)
    positions, fills, voids = _read(source, pseudonym_key, sessions, budget)
    v2 = bool(voids)
    schema, methodology = (SCHEMA_V2, METHOD_V2) if v2 else (SCHEMA, METHOD)
    payloads = {
        "positions.jsonl": b"".join((canonical(p) + "\n").encode() for p in positions),
        "fills.jsonl": b"".join((canonical(f) + "\n").encode() for f in fills),
        "methodology.json": (canonical(methodology) + "\n").encode(),
    }
    if v2:
        payloads["voids.jsonl"] = b"".join((canonical(v) + "\n").encode() for v in voids)
    session_rows = {session: {"fills": sum(f["session"] == session for f in fills),
                              "closed_positions": sum(p["exit_session"] == session for p in positions),
                              "source_coverage": "NO_VERIFICADO"}
                    for session in sessions}
    if v2:
        for session in sessions:
            session_rows[session]["admin_voids"] = sum(v["admin_cancel_day"] == session for v in voids)
    manifest = {
        "schema": schema, "calculation_version": CALCULATION,
        "mode": "PAPER/SHADOW", "source_access": "READ_ONLY/mode=ro/query_only/SELECT_ONLY",
        "real_orders_sent": 0, "real_routes": "NOT_CALLED", "runtime_mutation": False,
        "source_safety": "SNAPSHOT_PRODUCTION_PAPER_ZERO_REAL_ORDERS",
        "cohort": {"sessions": sessions, "session_count": 20, "timezone": str(ART),
                   "cutoff_exclusive": bounds(sessions)[1].isoformat(),
                   "selection": ("OVERLAPPING_SPOT_POSITIONS_WITH_ADMIN_VOIDS_V2"
                                 if v2 else "OVERLAPPING_SPOT_POSITIONS")},
        "session_rows": session_rows,
        "limits": {"positions": limits.positions, "fills": limits.fills, "bytes": limits.bytes,
                   "row_bytes": limits.row_bytes, "seconds": limits.seconds},
        "counts": {"positions": len(positions), "fills": len(fills),
                   "closed_positions": sum(p["lifecycle"] == "CLOSED" for p in positions),
                   "open_survivors": sum(p["lifecycle"] == "OPEN_AT_CUTOFF" for p in positions)},
        "currencies": sorted({p["currency"] for p in positions}), "currency_totals_combined": False,
        "files": {name: {"sha256": sha256(data), "bytes": len(data),
                         "rows": (len(positions) if name == "positions.jsonl" else
                                  len(fills) if name == "fills.jsonl" else
                                  len(voids) if name == "voids.jsonl" else 1)}
                  for name, data in sorted(payloads.items())},
        "missing_evidence": ["ACCOUNT_SPECIFIC_FEES", "COMMISSION_RIGHTS_VAT_BREAKDOWN", "FILL_PROVIDER_CLOCKS",
                             "UNITS_PER_LOT", "CASH_EQUITY_RECONCILIATION", "CORPORATE_ACTIONS", "SPECIALIZED_NONSPOT_LEDGERS"],
        "source_authentication": "EXTERNAL_EVIDENCE_PENDING_UNTIL_SOURCE_OWNER_RECONCILIATION",
        "truncated": False, "invented_rows": 0, "calibration_or_edge_claim": False,
    }
    if any(p["lineage"]["status"] == "NO_VERIFICADO" for p in positions):
        manifest["missing_evidence"].append("POSITION_CLOCK_OR_SOURCE_LINEAGE")
    if any(p["strategy"] == "NO_VERIFICADO" for p in positions):
        manifest["missing_evidence"].append("STRATEGY_ID")
    if any(p["exit_reason_status"] == "NO_VERIFICADO" for p in positions):
        manifest["missing_evidence"].append("LEGACY_EXIT_REASON_MAPPING")
    if v2:
        manifest["counts"]["admin_voids"] = len(voids)
        a207_proof = any(v['authority'] == A207_ADMIN_AUTHORITY for v in voids)
        manifest["admin_void_exclusions"] = {
            "count": len(voids), "trade_pnl_effect": "NONE",
            "source_proof": ("A207_SPOT_BATCH_MEMBERSHIP_FULL_BATCH_UNVERIFIED"
                             if a207_proof else "BOUNDED_READ_ONLY_SOURCE_RECEIPT_VERIFIED"),
            "external_authentication": "SOURCE_OWNER_RECONCILIATION_REQUIRED",
        }
        manifest["missing_evidence"].append("ADMIN_VOID_EXTERNAL_SOURCE_OWNER_RECONCILIATION")
        if a207_proof:
            manifest["missing_evidence"].append("A207_BATCH_FULL_TARGET_SET_COMPLETENESS_NO_VERIFICADO")
    payloads["manifest.json"] = (canonical(manifest) + "\n").encode()
    if sum(map(len, payloads.values())) > limits.bytes:
        raise EvidenceError("BYTE_BUDGET_EXHAUSTED")
    budget.check()
    staging = Path(tempfile.mkdtemp(prefix="." + destination.name + ".sanitized-", dir=parent))
    try:
        for name, data in sorted(payloads.items()):
            budget.check()
            _write_private(staging / name, data)
        from .recompute import verify_package
        report = verify_package(staging, expected_manifest_sha256=sha256(payloads["manifest.json"]), limits=limits,
                                _deadline=budget.deadline)
        budget.check()
        _fsync_directory(staging)
        # Renaming a populated directory gives readers all files in one cut.
        # A pre-existing destination is never a retry/overwrite authority.
        if destination.exists() or destination.is_symlink():
            raise EvidenceError("NEW_DESTINATION_REQUIRED")
        _publish(staging, destination)
        _fsync_directory(parent)
        result = {"status": "PACKAGE_RECOMPUTED", "manifest_sha256": sha256(payloads["manifest.json"]),
                "counts": manifest["counts"], "currencies": report["currencies"],
                "source_authentication": manifest["source_authentication"], "real_orders_sent": 0,
                "real_routes": "NOT_CALLED"}
        if v2:
            result["schema"] = schema
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)
