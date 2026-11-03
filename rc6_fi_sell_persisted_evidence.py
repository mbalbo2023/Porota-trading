"""Read-only, persisted-evidence bridge for BONOS/ON PAPER exit intentions.

This is an audit/guard at the *actual* exit supervisor boundary, not a sale
adapter.  Unlike a caller-supplied Book hash, the sources are a SQLite snapshot
and the scanner's private, bounded native PPI Book journal.  Hash-chain
integrity proves internal consistency, **not** a broker signature or unit.

There is deliberately no route to ``apply_paper_market_sale`` here.  A signed,
independently received post-COMMIT exit-decision receipt, an authenticated
quantity/price basis, account fee evidence, and settlement economics are not
yet in these stores.  All cases return ``execution_authorized=False``.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
import stat
from threading import RLock

from bs_instrument_contracts import aware_datetime, family_name, utc_microseconds
from cp_contract_evidence_v2_hf6 import evidence_hash
from cf_sale_settlement import modeled_sale_settlement_date
from rc6_ppi_scanner_provenance import (
    DEFAULT_PATH, NATIVE_QUOTE_MAX_AGE_SECONDS, PIT_JOURNAL_FILENAME,
    verified_native_book_journal,
)


SCHEMA = "rc6.fi-exit-persisted-book-intent-audit.v1"
PAPER_ONLY = True
REAL_ROUTES_USED = ()
SELL_AUTHORITY = False
_FI_FAMILIES = frozenset({"BONOS", "OBLIGACIONES"})
_PPI_IDENTITY_SOURCES = frozenset({
    "PPI_STRUCTURED_API", "PPI_AUTHENTICATED_XHR", "PPI_AUTHENTICATED_DOM",
    "PPI_AUTHENTICATED_WEB",
})
_DUE_STATES = frozenset({
    "EXIT_DUE", "EXIT_PENDING_FI_NATIVE_EVIDENCE", "EXIT_PENDING_EXECUTION",
    "EXIT_PENDING_MARKET_CLOSED", "EXIT_PENDING_NO_QUOTE",
    "EXIT_PENDING_NO_LIQUIDITY", "EXIT_PENDING_STALE_QUOTE",
})
_MAX_BLOCKERS = 20
_JOURNAL_INDEX_LOCK = RLock()


def _file_fingerprint(path):
    """Stable file identity; never follow symlinks or trust mtime alone."""
    try:
        s = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_mode & 0o077:
        return None
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
            s.st_mode, s.st_nlink, s.st_uid, s.st_gid)


def _compact_native_event(event):
    """Retain only audited best levels, never the bounded full-depth payload."""
    book = event["book"]
    return {"canonical_identity": tuple(event["canonical_identity"]),
            "native_book_at": event["native_book_at"],
            "known_at": event["known_at"],
            "event_sha256": event["event_sha256"],
            "quantity_unit": event["quantity_unit"],
            "price_unit": event["price_unit"],
            "book": {"bids": [dict(book["bids"][0])] if book["bids"] else [],
                     "offers": [dict(book["offers"][0])] if book["offers"] else []}}


@lru_cache(maxsize=1)
def _verified_compact_index(path_text, fingerprint):
    """One canonical parse per unchanged file; max one compact index in RAM.

    The cache key includes inode, size, mtime, ctime, permission and ownership.
    A fresh lstat after reading prevents associating new bytes with an old key.
    The cache is only a diagnostic optimization: provider PIT freshness and
    decision/units/fee authority are evaluated anew for *every* consumer.
    """
    path = Path(path_text)
    rows, receipt = verified_native_book_journal(path)
    if _file_fingerprint(path) != fingerprint:
        return (), {"state": "HOLD", "reason": "FI_EXIT_PPI_JOURNAL_CHANGED_DURING_INDEX"}
    if receipt.get("state") != "HASH_CHAIN_VERIFIED":
        return (), receipt
    return tuple(_compact_native_event(event) for event in rows), receipt


def _verified_journal_index(path):
    """Singleflight cache; absent/unsafe files still use canonical verifier."""
    with _JOURNAL_INDEX_LOCK:
        fingerprint = _file_fingerprint(path)
        if fingerprint is None:
            return verified_native_book_journal(path)
        # Recheck even on a cache hit: a changed file must never reuse the
        # previously indexed journal without a fresh canonical verification.
        rows, receipt = _verified_compact_index(str(path), fingerprint)
        if _file_fingerprint(path) != fingerprint:
            return (), {"state": "HOLD", "reason": "FI_EXIT_PPI_JOURNAL_CHANGED_DURING_LOOKUP"}
        return rows, receipt


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (name,)).fetchone() is not None


def _us(value):
    try:
        return utc_microseconds(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value, *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite() or (positive and result <= 0) or result < 0:
        return None
    return result


def _strict_json(raw):
    def no_duplicate(pairs):
        item = {}
        for key, value in pairs:
            if key in item:
                raise ValueError("FI_EXIT_CONTRACT_DUPLICATE_JSON_KEY")
            item[key] = value
        return item

    def no_nonfinite(_):
        raise ValueError("FI_EXIT_CONTRACT_NONFINITE_JSON")

    return json.loads(raw, object_pairs_hook=no_duplicate, parse_constant=no_nonfinite)


def _snapshot_book_matches(snapshot, event):
    if (_us(snapshot.get("observed_at")) != _us(event.get("known_at"))
            or _us(snapshot.get("book_at")) != _us(event.get("native_book_at"))):
        return False
    book = event.get("book") or {}
    for levels, price, size in ((book.get("bids"), "bid", "bid_size"),
                                (book.get("offers"), "ask", "ask_size")):
        if not isinstance(levels, list):
            return False
        best = levels[0] if levels else {"price": "0", "quantity": "0"}
        if (_number(snapshot.get(price)) is None or
                _number(snapshot.get(price)) != _number(best.get("price")) or
                _number(snapshot.get(size)) is None or
                _number(snapshot.get(size)) != _number(best.get("quantity"))):
            return False
    return True


def _identity(position):
    family = family_name(position["asset_class"])
    if family not in _FI_FAMILIES:
        raise ValueError("FI_EXIT_FAMILY_NOT_BONOS_OR_ON")
    raw = (position["symbol"], family, position["market"],
           position["currency"], position["settlement"])
    if any(not isinstance(part, str) or not part or part in ("UNKNOWN", "*")
           for part in raw):
        raise ValueError("FI_EXIT_PPI_FIVE_KEY_INCOMPLETE")
    return raw


def _latency_us(value):
    latency = _number(value)
    if latency is None or latency > 300 or latency * 1_000_000 != (latency * 1_000_000).to_integral_value():
        raise ValueError("FI_EXIT_LATENCY_MICROSECONDS_INVALID")
    return int(latency * 1_000_000)


def inspect_persisted_fi_exit(store, paper_id, *, at, journal_path=None,
                              latency_seconds="1"):
    """Reconcile stored exit+snapshot+contract+native journal; never authorize SELL.

    ``at`` is the supervisor's evaluation cutoff, not a signed decision time.
    The provisional Book2 cutoff is diagnostic only; it cannot authorize fill.
    The private journal is read directly (no provider GET) with integrity and
    file-permission checks delegated to its canonical verifier.
    """
    blockers = []
    anchors = {}

    def hold(reason):
        if reason not in blockers and len(blockers) < _MAX_BLOCKERS:
            blockers.append(reason)

    def result():
        return {"schema": SCHEMA, "paper_only": True,
                "execution_authorized": False, "real_routes_used": [],
                "status": "BLOCKED", "paper_id": paper_id,
                "blockers": blockers[:], "anchors": anchors.copy(),
                "decision_known_at_independently_verified": False,
                "book2_is_execution_authority": False}

    if not isinstance(paper_id, str) or not paper_id.startswith("PAPER-"):
        hold("FI_EXIT_PAPER_ID_INVALID")
        return result()
    try:
        cutoff = utc_microseconds(aware_datetime(at))
        latency = _latency_us(latency_seconds)
    except (ValueError, TypeError, OverflowError):
        hold("FI_EXIT_AUDIT_CLOCK_OR_LATENCY_INVALID")
        return result()

    snapshot = None
    intent = None
    identity = None
    with closing(store.connect()) as db:
        try:
            db.execute("BEGIN")
            required = ("observer_state", "paper_positions", "paper_exit_intents",
                        "market_snapshots")
            if any(not _table(db, name) for name in required):
                hold("FI_EXIT_LEDGER_SCHEMA_MISSING")
                return result()
            safety = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
            if (not safety or safety[0] != "PRODUCTION_PAPER" or
                    type(safety[1]) is not int or safety[1] != 0):
                hold("FI_EXIT_PAPER_ZERO_REAL_ORDERS_REQUIRED")
            position_row = db.execute("SELECT * FROM paper_positions WHERE paper_id=?",
                                      (paper_id,)).fetchone()
            if not position_row:
                hold("FI_EXIT_POSITION_NOT_PERSISTED")
                return result()
            position = dict(position_row)
            if position.get("status") != "OPEN" or position.get("closed_at"):
                hold("FI_EXIT_POSITION_NOT_OPEN")
            identity = _identity(position)
            anchors["identity"] = list(identity)
            qty = _number(position.get("quantity"), positive=True)
            if qty is None:
                hold("FI_EXIT_POSITION_QUANTITY_INVALID")
            else:
                # This is the recorded PAPER fill quantity, not accredited VN.
                anchors["position_recorded_quantity_not_verified_vn"] = str(qty)
            if not _table(db, "paper_fills"):
                hold("FI_EXIT_PAPER_FILL_LEDGER_MISSING")
            else:
                fills = db.execute("SELECT source,side,quantity FROM paper_fills WHERE paper_id=?",
                                   (paper_id,)).fetchall()
                buys = [fill for fill in fills if fill[1] == "BUY_SIMULATED"]
                sells = [fill for fill in fills if fill[1] == "SELL_SIMULATED"]
                if (len(buys) != 1 or len(fills) != 1 + len(sells) or
                        any(fill[0] != "PRODUCTION_PAPER" for fill in fills) or
                        qty is None or _number(buys[0][2], positive=True) != qty):
                    hold("FI_EXIT_IMMUTABLE_BUY_PAPER_LEDGER_INVALID")
                anchors["prior_paper_sell_fill_count"] = len(sells)
            intent_row = db.execute("SELECT * FROM paper_exit_intents WHERE paper_id=?",
                                    (paper_id,)).fetchone()
            if not intent_row:
                hold("FI_EXIT_INTENT_NOT_PERSISTED")
            else:
                intent = dict(intent_row)
                anchors["intent_state"] = intent.get("state")
                anchors["intent_cause"] = intent.get("cause")
                if intent.get("state") not in _DUE_STATES or not intent.get("cause"):
                    hold("FI_EXIT_INTENT_NOT_DUE")
                if _us(intent.get("supervised_at")) is None or _us(intent.get("supervised_at")) > cutoff:
                    hold("FI_EXIT_INTENT_CLOCK_INVALID")
                # persisted 'supervised_at' is quote/evaluation time supplied by
                # the same caller. It is NOT a post-COMMIT, independent ACK.
                anchors["intent_clock_basis"] = "CALLER_SUPPLIED_NOT_POSTCOMMIT_RECEIPT"
            snapshot_row = db.execute("""SELECT * FROM market_snapshots
               WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?
               ORDER BY id DESC LIMIT 1""", identity).fetchone()
            if snapshot_row is None:
                hold("FI_EXIT_EXACT_PAPER_SNAPSHOT_MISSING")
            else:
                snapshot = dict(snapshot_row)
                anchors["market_snapshot_id"] = snapshot["id"]
                if (snapshot.get("source") != "PRODUCTION_PAPER" or
                        _us(snapshot.get("observed_at")) is None or
                        _us(snapshot.get("observed_at")) > cutoff or
                        _us(snapshot.get("book_at")) is None or
                        _us(snapshot.get("book_at")) > _us(snapshot.get("observed_at"))):
                    hold("FI_EXIT_EXACT_PAPER_SNAPSHOT_CLOCK_OR_MODE_INVALID")
                # 'source' is the PAPER MODE, never proof of provider=PPI.
                anchors["snapshot_source_is_mode_only"] = True
            try:
                expected_day = modeled_sale_settlement_date(identity[4], at)
            except (ValueError, TypeError, OverflowError, ImportError):
                expected_day = None
            if expected_day is None:
                hold("FI_EXIT_PAPER_SETTLEMENT_CALENDAR_UNVERIFIED")
            else:
                anchors["paper_modeled_settlement_date_not_broker_receipt"] = expected_day
            contract_family = "ON" if identity[1] == "OBLIGACIONES" else "BONOS"
            contract_key = (contract_family, identity[0], identity[2], identity[3], identity[4])
            if (not _table(db, "contract_evidence_v2_current") or
                    not _table(db, "contract_evidence_v2_snapshots")):
                hold("FI_EXIT_CONTRACT_V2_SCHEMA_MISSING")
            else:
                rows = db.execute("""SELECT c.source_class,c.snapshot_id,c.evidence_hash,
                     c.observed_at,s.source_ref,s.evidence_json,s.evidence_hash
                     FROM contract_evidence_v2_current c JOIN contract_evidence_v2_snapshots s
                       ON s.snapshot_id=c.snapshot_id
                     WHERE c.family=? AND c.ticker=? AND c.market=? AND c.currency=?
                       AND c.settlement=?""", contract_key).fetchall()
                valid = []
                for raw in rows:
                    try:
                        payload = _strict_json(raw[5])
                        if (not isinstance(payload, dict) or
                                raw[2] != raw[6] or evidence_hash(payload) != raw[2] or
                                _us(raw[3]) is None or _us(raw[3]) > cutoff):
                            raise ValueError("contract hash/clock mismatch")
                    except (ValueError, TypeError):
                        hold("FI_EXIT_CONTRACT_V2_HASH_OR_PIT_INVALID")
                        continue
                    if raw[0] in _PPI_IDENTITY_SOURCES and raw[4]:
                        valid.append((int(raw[1]), raw[2]))
                if not valid:
                    hold("FI_EXIT_PPI_FIVE_KEY_CONTRACT_NOT_VERIFIED")
                else:
                    anchors["ppi_contract_snapshot_ids"] = [item[0] for item in valid]
                    anchors["ppi_contract_hashes"] = [item[1] for item in valid]
                if _table(db, "contract_evidence_v2_changes"):
                    change = db.execute("""SELECT 1 FROM contract_evidence_v2_changes
                       WHERE family=? AND ticker=? AND market=? AND currency=?
                         AND settlement=? AND status='CHANGED_REVIEW_REQUIRED' LIMIT 1""",
                                        contract_key).fetchone()
                    if change:
                        hold("FI_EXIT_CONTRACT_MATERIAL_CHANGE_REVIEW_PENDING")
                else:
                    hold("FI_EXIT_CONTRACT_CHANGE_LEDGER_MISSING")
            if _table(db, "paper_fi_native_sales"):
                prior = db.execute("SELECT COUNT(*) FROM paper_fi_native_sales WHERE paper_id=?",
                                   (paper_id,)).fetchone()[0]
                anchors["prior_native_sale_receipt_count"] = prior
                if prior:
                    hold("FI_EXIT_PRIOR_SALES_REQUIRE_INDEPENDENT_LEDGER_RECONCILIATION")
                if _table(db, "paper_fills") and prior != anchors.get("prior_paper_sell_fill_count",0):
                    hold("FI_EXIT_SELL_FILL_NATIVE_RECEIPT_COUNT_MISMATCH")
            elif anchors.get("prior_paper_sell_fill_count",0):
                hold("FI_EXIT_NATIVE_SALE_LEDGER_MISSING_WITH_SELL_FILLS")
        except (sqlite3.Error, KeyError, ValueError, TypeError):
            hold("FI_EXIT_DB_EVIDENCE_INVALID_OR_UNREADABLE")
            return result()

    path = Path(journal_path) if journal_path is not None else DEFAULT_PATH.with_name(PIT_JOURNAL_FILENAME)
    if not path.is_absolute():
        hold("FI_EXIT_PPI_BOOK_JOURNAL_PATH_NOT_ABSOLUTE")
        return result()
    rows, receipt = _verified_journal_index(path)
    anchors["journal_state"] = receipt.get("state")
    if receipt.get("state") != "HASH_CHAIN_VERIFIED":
        hold("FI_EXIT_PPI_NATIVE_JOURNAL_" + str(receipt.get("reason") or "UNVERIFIED")[:85])
    elif identity is not None:
        matching = [event for event in rows if tuple(event.get("canonical_identity") or ()) == identity]
        anchors["ppi_native_same_identity_events"] = len(matching)
        book1 = ([event for event in matching if snapshot and _snapshot_book_matches(snapshot,event)]
                 if intent else [])
        if not book1:
            hold("FI_EXIT_BOOK1_SNAPSHOT_NATIVE_JOURNAL_NOT_LINKED")
        else:
            matched = book1[-1]
            anchors["book1_event_sha256"] = matched["event_sha256"]
            if _us(matched["known_at"]) > _us(intent["supervised_at"]):
                hold("FI_EXIT_BOOK1_NOT_KNOWN_BEFORE_INTENT")
            if matched.get("quantity_unit") != "VN":
                hold("FI_EXIT_PPI_BOOK_QUANTITY_VN_UNVERIFIED")
            if matched.get("price_unit") != "PRICE_PER_100_VN":
                hold("FI_EXIT_PPI_BOOK_PRICE_PER100_UNVERIFIED")
        if intent and _us(intent.get("supervised_at")) is not None:
            # Deliberately only a *provisional* clock pair. The signed decision
            # ACK can be later; Book2 must ultimately be tested against
            # max(decision-native-at, independently verified known-at)+latency.
            threshold = _us(intent["supervised_at"]) + latency
            future = [event for event in matching if
                      _us(event.get("native_book_at")) is not None and
                      _us(event["native_book_at"]) > threshold and
                      _us(event.get("known_at")) is not None and
                      _us(event["known_at"]) > _us(intent["supervised_at"])]
            if future:
                anchors["provisional_book2_event_sha256"] = future[0]["event_sha256"]
                age_us = cutoff - _us(future[0]["native_book_at"])
                if not 0 <= age_us <= NATIVE_QUOTE_MAX_AGE_SECONDS * 1_000_000:
                    hold("FI_EXIT_BOOK2_NATIVE_STALE_AT_AUDIT")
                levels = (future[0].get("book") or {}).get("bids") or []
                if (not levels or _number(levels[0].get("price"), positive=True) is None or
                        _number(levels[0].get("quantity"), positive=True) is None):
                    hold("FI_EXIT_BOOK2_NATIVE_BID_OR_DEPTH_NOT_EXECUTABLE")
            else:
                hold("FI_EXIT_BOOK2_AFTER_PROVISIONAL_LATENCY_MISSING")

    # These distinct barriers are not represented by an independently signed
    # exit receipt, a PPI-specific VN/price economic estimate, or ALyC account
    # tariff in the *current* SQLite schemas. A hash supplied by this process
    # cannot satisfy them. Modelled settlement != broker cash accreditation.
    hold("FI_EXIT_DECISION_POSTCOMMIT_SIGNED_RECEIPT_MISSING")
    hold("FI_EXIT_PPI_UNIT_ECONOMIC_RECEIPT_UNVERIFIED")
    hold("FI_EXIT_ALYC_ACCOUNT_TARIFF_UNVERIFIED")
    hold("FI_EXIT_SETTLEMENT_CURRENCY_AND_MODEL_RECEIPT_UNVERIFIED")
    return result()
