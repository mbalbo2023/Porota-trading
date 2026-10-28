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
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
HOST_ROOT = Path("/opt/porota-trading")
HOST_RECEIPT = HOST_ROOT / "data/deploy/paper_scalping_preopen_gate_v1.json"
RUNTIME_RECEIPT = Path("/app/data/deploy/paper_scalping_preopen_gate_v1.json")
FOCUS = (
    ("GGAL", "ACCIONES", "A-24HS"), ("YPFD", "ACCIONES", "A-24HS"),
    ("PAMP", "ACCIONES", "A-24HS"), ("BMA", "ACCIONES", "A-24HS"),
    ("BBAR", "ACCIONES", "A-24HS"), ("SUPV", "ACCIONES", "A-24HS"),
    ("CEPU", "ACCIONES", "A-24HS"), ("AAPL", "CEDEARS", "A-24HS"),
)
SHA = re.compile(r"[0-9a-f]{40}\Z")
# Date-scoped operator decisions: no new PAPER positions on 27-Oct (A125)
# or 28-Oct (A151) while the candidate is repaired and validated. The veto is
# independent of a timer's T-10 GO, including one from a newly deployed image.
# Never carry it automatically to another trading day.
OPERATOR_PAUSED_TRADE_DAYS_AR = frozenset({"2026-10-27", "2026-10-28"})


def operator_pause_reason(now):
    day = now.date().isoformat()
    return ("OPERATOR_PAPER_PAUSE_" + day.replace("-", "_")
            if day in OPERATOR_PAUSED_TRADE_DAYS_AR else "")


def focus_contracts(db_path):
    """Require every configured focus identity to be unambiguous and ready."""
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_identity_v2'").fetchone():
                return {"state": "RED", "reason": "EXACT_CANDIDATE_LEDGER_MISSING"}
            missing = []
            for ticker, kind, settlement in FOCUS:
                rows = db.execute("""SELECT f.market,f.currency FROM financial_instrument_catalog f
                    JOIN candidate_identity_v2 c USING(ticker,instrument_type,market,currency,settlement)
                    WHERE f.ticker=? AND f.instrument_type=? AND f.settlement=?
                      AND f.status='AVAILABLE' AND f.capability='READY_PAPER_SPOT'
                      AND c.status='AVAILABLE' AND c.can_simulate=1""",
                    (ticker, kind, settlement)).fetchall()
                if len(rows) != 1:
                    missing.append(ticker)
            return {"state": "GREEN" if not missing else "RED",
                    "ready": len(FOCUS) - len(missing), "required": len(FOCUS),
                    "missing": missing}
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {"state": "RED", "reason": type(exc).__name__}


def publish_preopen(result, *, now=None, state_path=None, receipt_path=None):
    """Atomically publish a closed receipt, opening it only for a full T-10 GO."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    state_path = Path(state_path or HOST_ROOT / "data/deploy/CURRENT_STATE_V2.json")
    receipt_path = Path(receipt_path or HOST_RECEIPT)
    candidate = ""
    try:
        if state_path.is_symlink() or state_path.stat().st_size > 65536:
            raise ValueError("UNSAFE_RELEASE_STATE")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        candidate = state.get("candidate_sha", "")
        valid_release = (isinstance(candidate, str) and SHA.fullmatch(candidate)
                         and state.get("validation_status") == "VALIDATED_RUNTIME"
                         and state.get("mode") == "PRODUCTION_PAPER"
                         and state.get("real_orders_sent") == 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        valid_release = False
    pause_reason = operator_pause_reason(now)
    allowed = (not pause_reason and result.get("status") == "GREEN" and result.get("phase") == "T_MINUS_10"
               and 10 * 60 + 20 <= now.hour * 60 + now.minute < 10 * 60 + 30
               and result.get("checks", {}).get("paper_focus_contracts", {}).get("state") == "GREEN"
               and valid_release)
    receipt = {"schema": "RC6_PAPER_PREOPEN_GATE_V1", "status":
               "GO_PAPER_SCALPING" if allowed else "NO_GO_PAPER_SCALPING",
               "candidate_sha": candidate if valid_release else "", "trade_day_ar": now.date().isoformat(),
               "issued_at_ar": now.isoformat(timespec="seconds"), "phase": result.get("phase"),
               "reason": (pause_reason if pause_reason else
                          "PREOPEN_GREEN_EXACT_FOCUS" if allowed else "PREOPEN_OR_RELEASE_NOT_GREEN")}
    # A125's 27-Oct LIVE gate is an immutable link in the A138/A143 scheduler
    # recovery chain. Neither a manual preopen nor a subsequent day's T-10
    # may overwrite it while that chain is active. The separate A151 sidecar
    # attests today's NO_GO; any future rearm requires a reviewed migration.
    if (receipt_path == HOST_RECEIPT and
            now.date().isoformat() >= "2026-10-27"):
        raise ValueError("HISTORICAL_A125_LIVE_GATE_IMMUTABLE")
    if receipt_path.parent.is_symlink() or not receipt_path.parent.is_dir() or receipt_path.is_symlink():
        raise ValueError("UNSAFE_GATE_PATH")
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
    path = Path(receipt_path or RUNTIME_RECEIPT)
    if not isinstance(candidate_sha, str) or not SHA.fullmatch(candidate_sha):
        return False, "PAPER_PREOPEN_BUILD_IDENTITY_MISSING"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return False, "PAPER_PREOPEN_RECEIPT_UNSAFE"
        receipt = json.loads(path.read_text(encoding="utf-8"))
        issued = datetime.fromisoformat(receipt["issued_at_ar"])
        if issued.tzinfo is None or issued.utcoffset() != now.utcoffset():
            raise ValueError("PREOPEN_CLOCK_INVALID")
        issued = issued.astimezone(TZ)
        good = (receipt.get("schema") == "RC6_PAPER_PREOPEN_GATE_V1"
                and receipt.get("status") == "GO_PAPER_SCALPING"
                and receipt.get("phase") == "T_MINUS_10"
                and receipt.get("candidate_sha") == candidate_sha
                and receipt.get("trade_day_ar") == now.date().isoformat()
                and issued.date() == now.date()
                and 10 * 60 + 20 <= issued.hour * 60 + issued.minute < 10 * 60 + 30
                and 0 <= (now - issued).total_seconds() <= 7 * 3600)
        return (True, "") if good else (False, "NO_GO_PAPER_SCALPING")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False, "PAPER_PREOPEN_RECEIPT_INVALID"


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
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
        return False, "PAPER_EXACT_CONTRACT_UNAVAILABLE"
    return True, ""
