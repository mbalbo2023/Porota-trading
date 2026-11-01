"""Bounded offhost ten-family historical review, never trading authorization.

The retrospective counterfactual receipt checks economics only. Causal PIT
requires authentic contemporaneous availability proof and is deliberately
not inferred from today's historical records or synthetic fixtures.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile


SCHEMA = "rc6.a311.history.pit.offhost.v1"
FAMILIES = (
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
    "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
)
MAX_BYTES = 64 * 1024 * 1024
MAX_CAPTURES = 40
MAX_ROWS_PER_CAPTURE = 1000
MAX_TOTAL_ROWS = 10000
MAX_DB_STEPS = 300000
ADMITTED_SOURCES = frozenset({"PPI", "IOL", "IOL_CONNECTOR", "A3_CEM", "BYMA", "DATA912"})
PER_ROW_CURRENCY = ("currency", "quoteCurrency", "moneda")


def _utc(raw, label):
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"A311_{label}_CLOCK_UNVERIFIED") from None


def _decimal(value, label, *, positive=True):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"A311_{label}_MISSING")
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise ValueError(f"A311_{label}_INVALID") from None
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise ValueError(f"A311_{label}_INVALID")
    return result


def _value(row, *names):
    present = [(name, row[name]) for name in names if name in row]
    if len(present) != 1:
        raise ValueError("A311_AMBIGUOUS_OR_MISSING_FIELD")
    return present[0][1]


def _row_day(row, source):
    key = {"IOL": "fechaHora", "A3_CEM": "dateTime"}.get(source, "date")
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError("A311_ROW_DAY_UNVERIFIED")
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise ValueError("A311_ROW_DAY_UNVERIFIED") from None


def _historical_ohlcv(row, source):
    if not isinstance(row, dict):
        return False
    aliases = (
        {"open": ("openingPrice",), "high": ("max",), "low": ("min",),
         "close": ("price",), "volume": ("volume",)}
        if source == "PPI" else
        {"open": ("open",), "high": ("high",), "low": ("low",),
         "close": ("close",), "volume": ("volume",)}
    )
    try:
        values = {
            name: _decimal(_value(row, *keys), name, positive=(name != "volume"))
            for name, keys in aliases.items()
        }
    except ValueError:
        return False
    return (values["low"] <= values["open"] <= values["high"]
            and values["low"] <= values["close"] <= values["high"])


def _historical_economic_record(row, family, source):
    """Require family-specific economics; OHLC never implies a fill."""
    if not isinstance(row, dict):
        return False
    if family == "CAUCIONES":
        try:
            tna = _decimal(row["tna_pct"], "TNA", positive=False)
            principal = _decimal(row["principal"], "PRINCIPAL")
            days = row["term_days"]
            maturity = date.fromisoformat(row["maturity_date"])
            return (Decimal(0) <= tna <= Decimal(1000) and principal > 0
                    and type(days) is int and 1 <= days <= 120
                    and maturity > _row_day(row, source))
        except (KeyError, ValueError, TypeError):
            return False
    if family == "FCI":
        try:
            nav = _decimal(row["nav_per_unit"], "NAV")
            klass = row["fund_class_id"]
            cutoff = _utc(row["fund_cutoff_at_utc"], "FUND_CUTOFF")
            return (nav > 0 and isinstance(klass, str) and 0 < len(klass) <= 80
                    and cutoff.date() >= _row_day(row, source))
        except (KeyError, ValueError, TypeError):
            return False
    if family == "FUTUROS":
        try:
            return (_decimal(row["settlementPrice"], "SETTLEMENT_PRICE") > 0
                    and _decimal(row["contractMultiplier"], "CONTRACT_MULTIPLIER") > 0
                    and date.fromisoformat(row["expirationDate"]) > _row_day(row, source))
        except (KeyError, ValueError, TypeError):
            return False
    if family == "OPCIONES":
        try:
            return (_decimal(row["premium"], "PREMIUM") > 0
                    and _decimal(row["strike"], "STRIKE") > 0
                    and _decimal(row["contractMultiplier"], "CONTRACT_MULTIPLIER") > 0
                    and date.fromisoformat(row["expirationDate"]) > _row_day(row, source))
        except (KeyError, ValueError, TypeError):
            return False
    return _historical_ohlcv(row, source)


def _identity_row_verified(row, capture):
    if not isinstance(row, dict):
        return False
    expected = {
        "currency": capture["currency"], "market": capture["market"],
        "settlement": capture["settlement"], "family": capture["family"],
        "symbol": capture["symbol"],
    }
    required = {
        "symbol": ("symbol", "ticker"),
        "family": ("instrumentType", "type"),
        "market": ("market",),
        "settlement": ("settlementTerm",),
        "currency": PER_ROW_CURRENCY,
    }
    for name, aliases in required.items():
        values = {str(row[alias]).strip().upper() for alias in aliases
                  if alias in row and row[alias] not in (None, "")}
        if values != {expected[name]}:
            return False
    return (row.get("priceBasis") == "RAW"
            and row.get("adjustmentBasis") == "RAW_NO_ADJUSTMENT"
            and row.get("adjusted") is False)


def _known_and_published(row, captured_at, decision_at, economic_day):
    try:
        published = _utc(row.get("published_at_utc"), "PUBLICATION")
        known = _utc(row.get("known_at_utc"), "ROW_KNOWLEDGE")
    except ValueError:
        return "ROW_PUBLICATION_OR_KNOWN_AT_UNVERIFIED"
    if row.get("bar_kind") != "EOD":
        return "DAILY_BAR_PUBLICATION_KIND_UNVERIFIED"
    try:
        close = _utc(row.get("session_close_at_utc"), "SESSION_CLOSE")
        valid_from = date.fromisoformat(row["schedule_valid_from"])
        valid_until = date.fromisoformat(row["schedule_valid_through"])
    except (KeyError, TypeError, ValueError):
        return "EOD_SEGMENT_SCHEDULE_UNVERIFIED"
    digest = row.get("schedule_source_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return "EOD_SEGMENT_SCHEDULE_UNVERIFIED"
    if not valid_from <= economic_day <= valid_until:
        return "EOD_SEGMENT_SCHEDULE_EFFECTIVITY_MISMATCH"
    if close.date() < economic_day or published < close:
        return "EOD_PUBLISHED_BEFORE_SEGMENT_CLOSE"
    if known > captured_at or published > captured_at:
        return "SOURCE_CLOCK_AFTER_CAPTURE"
    if max(known, published, captured_at) > decision_at:
        return "RECEIPT_AFTER_HISTORICAL_DECISION"
    return None


def _sha(path):
    h = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_sidecar(path):
    target = Path(path).absolute()
    if target.resolve() != target or not target.is_file():
        raise ValueError("A311_PRIVATE_SIDECAR_PATH_UNSAFE")
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
        raise ValueError("A311_PRIVATE_SIDECAR_PATH_UNSAFE")
    before = _sha(target)
    conn = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=1)
    conn.execute("PRAGMA query_only=ON")
    conn.set_progress_handler(lambda: 1, MAX_DB_STEPS)
    try:
        expected = {"fresh_captures_rc6", "fresh_row_revisions_rc6"}
        present = {item[0] for item in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)",
            tuple(sorted(expected)))}
        if present != expected:
            raise ValueError("A311_SOURCE_SIDECAR_SCHEMA_UNVERIFIED")
        rows = conn.execute("""SELECT id,source,symbol,instrument_type,market,currency,
            settlement,requested_from,requested_to,first_known_at,
            retained_rows_json,retained_rows,provider_currency_status,
            price_basis_status,execution_allowed FROM fresh_captures_rc6
            ORDER BY id LIMIT ?""", (MAX_CAPTURES + 1,)).fetchall()
        if len(rows) > MAX_CAPTURES:
            raise ValueError("A311_CAPTURE_BUDGET_EXCEEDED")
        captures = []
        count = 0
        for columns in rows:
            (cid, source, symbol, family, market, currency, settlement,
             requested_from, requested_to, first_known_at, raw, stored_count,
             monetary, basis, execution_allowed) = columns
            if source not in ADMITTED_SOURCES or family not in FAMILIES or execution_allowed != 0:
                raise ValueError("A311_SOURCE_POLICY_OR_FAMILY_UNVERIFIED")
            payload = json.loads(raw)
            if not isinstance(payload, list) or len(payload) > MAX_ROWS_PER_CAPTURE:
                raise ValueError("A311_ROW_BUDGET_EXCEEDED")
            if len(payload) != stored_count:
                raise ValueError("A311_SOURCE_ROW_COUNT_MISMATCH")
            revisions = conn.execute("""SELECT retained_ordinal,economic_day,
                canonical_row_sha256,first_known_at FROM fresh_row_revisions_rc6
                WHERE capture_id=? ORDER BY retained_ordinal""", (cid,)).fetchall()
            if len(revisions) != len(payload):
                raise ValueError("A311_SOURCE_REVISION_COUNT_MISMATCH")
            for ordinal, (item, rev) in enumerate(zip(payload, revisions)):
                index, day, digest, known = rev
                actual = sha256(json.dumps(item, sort_keys=True,
                    separators=(",", ":"), ensure_ascii=False,
                    allow_nan=False).encode()).hexdigest()
                if (index != ordinal or actual != digest or known != first_known_at
                        or day != _row_day(item, source).isoformat()):
                    raise ValueError("A311_SOURCE_REVISION_HASH_OR_DAY_MISMATCH")
            count += len(payload)
            if count > MAX_TOTAL_ROWS:
                raise ValueError("A311_TOTAL_ROW_BUDGET_EXCEEDED")
            captures.append({
                "source": source, "symbol": symbol, "family": family,
                "market": market, "currency": currency,
                "settlement": settlement, "from": requested_from,
                "to": requested_to, "received_at": first_known_at,
                "rows": payload, "monetary_status": monetary,
                "basis_status": basis,
            })
    except sqlite3.Error as exc:
        raise ValueError("A311_SOURCE_SQLITE_INVALID") from exc
    finally:
        conn.close()
    if before != _sha(target):
        raise ValueError("A311_PRIVATE_SIDECAR_MUTATED")
    return captures, before


def review(sidecar, *, decision_at):
    decision = _utc(decision_at, "DECISION")
    captures, digest = _read_sidecar(sidecar)
    stats = {
        family: {
            "captures": 0, "retrospective_rows": 0,
            "temporal_contract_rows": 0, "excluded_rows": 0,
            "last_source_received_at_utc": None, "reasons": Counter(),
        }
        for family in FAMILIES
    }
    for capture in captures:
        bucket = stats[capture["family"]]
        bucket["captures"] += 1
        captured = _utc(capture["received_at"], "CAPTURE")
        last = bucket["last_source_received_at_utc"]
        if last is None or captured > _utc(last, "CAPTURE"):
            bucket["last_source_received_at_utc"] = captured.isoformat()
        for row in capture["rows"]:
            if not _historical_economic_record(row, capture["family"], capture["source"]):
                reasons = ["HISTORICAL_VALUE_UNVERIFIED"]
            else:
                bucket["retrospective_rows"] += 1
                reasons = []
                if not _identity_row_verified(row, capture):
                    reasons.append("PER_ROW_MONETARY_AND_CONTRACT_UNVERIFIED")
                if captured > decision:
                    reasons.append("RECEIPT_AFTER_HISTORICAL_DECISION")
                temporal = _known_and_published(
                    row, captured, decision, _row_day(row, capture["source"]))
                if temporal and temporal not in reasons:
                    reasons.append(temporal)
            if not reasons:
                bucket["temporal_contract_rows"] += 1
            else:
                bucket["excluded_rows"] += 1
                for reason in reasons:
                    bucket["reasons"][reason] += 1
    families = {}
    for family, bucket in stats.items():
        if not bucket["captures"]:
            bucket["reasons"]["FAMILY_SOURCE_ABSENT"] += 1
        families[family] = {
            "captures": bucket["captures"],
            "retrospective_rows": bucket["retrospective_rows"],
            "causal_pit_rows": 0,
            "temporal_contract_candidate_rows": bucket["temporal_contract_rows"],
            "excluded_rows": bucket["excluded_rows"],
            "reasons": dict(sorted(bucket["reasons"].items())),
            "paper_ready_inferred": False,
            "simulated_fills": 0,
            "last_offhost_received_at_utc": bucket["last_source_received_at_utc"],
            "canonical_ingestion_cursor_advanced": False,
        }
    return {
        "schema": SCHEMA,
        "source_sidecar_sha256": digest,
        "source_kind": "PRIVATE_OFFHOST_EVIDENCE_SIDECAR_ONLY",
        "decision_at_utc": decision.isoformat(),
        "family_denominator": len(FAMILIES),
        "capture_count": len(captures),
        "retrospective_counterfactual": {
            "schema": "RETROSPECTIVE_COUNTERFACTUAL",
            "family_count": len(FAMILIES),
            "rows_validated": sum(x["retrospective_rows"] for x in families.values()),
            "ready_inferred": False,
        },
        "causal_pit": {
            "schema": "CAUSAL_PIT",
            "family_count": len(FAMILIES),
            "rows_verified": 0,
            "temporal_contract_candidate_rows": sum(
                x["temporal_contract_candidate_rows"] for x in families.values()),
            "independent_contemporaneous_provider_receipt": False,
            "ready_inferred": False, "fills": 0, "real_orders_sent": 0,
        },
        "families": families, "history_v2_writes": 0, "host_access": False,
        "order_routes_called": False, "source_evidence_state": "NO_VERIFICADO",
    }


def _atomic_receipt(path, receipt):
    target = Path(path).absolute()
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("A311_RECEIPT_DESTINATION_UNSAFE")
    handle, temporary = tempfile.mkstemp(prefix=".rc6-a311-", dir=target.parent)
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def absent_source_receipt(*, decision_at, reason):
    if reason not in {"READONLY_CREDENTIAL_PAIR_ABSENT", "SOURCE_PROBE_UNAVAILABLE"}:
        raise ValueError("A311_UNREVIEWED_ABSENCE_REASON")
    decision = _utc(decision_at, "DECISION")
    families = {
        family: {
            "captures": 0, "retrospective_rows": 0, "causal_pit_rows": 0,
            "temporal_contract_candidate_rows": 0, "excluded_rows": 0,
            "reasons": {reason: 1}, "paper_ready_inferred": False,
            "simulated_fills": 0, "last_offhost_received_at_utc": None,
            "canonical_ingestion_cursor_advanced": False,
        }
        for family in FAMILIES
    }
    return {
        "schema": SCHEMA, "source_sidecar_sha256": None,
        "source_kind": "PRIVATE_OFFHOST_EVIDENCE_SIDECAR_ABSENT",
        "decision_at_utc": decision.isoformat(), "family_denominator": 10,
        "capture_count": 0,
        "retrospective_counterfactual": {
            "schema": "RETROSPECTIVE_COUNTERFACTUAL", "family_count": 10,
            "rows_validated": 0, "ready_inferred": False,
        },
        "causal_pit": {
            "schema": "CAUSAL_PIT", "family_count": 10,
            "rows_verified": 0, "temporal_contract_candidate_rows": 0,
            "independent_contemporaneous_provider_receipt": False,
            "ready_inferred": False, "fills": 0, "real_orders_sent": 0,
        },
        "families": families, "history_v2_writes": 0,
        "host_access": False, "order_routes_called": False,
        "source_evidence_state": "NO_VERIFICADO",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sidecar", type=Path, required=True)
    parser.add_argument("--decision-at-utc", required=True)
    parser.add_argument("--sanitized-receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = review(args.source_sidecar, decision_at=args.decision_at_utc)
    _atomic_receipt(args.sanitized_receipt, receipt)
    print("RC6_A311_HISTORY_PIT=EVIDENCE_ONLY|families=10|"
          f"retro={receipt['retrospective_counterfactual']['rows_validated']}|"
          f"causal_verified={receipt['causal_pit']['rows_verified']}|"
          "fills=0|history_v2_writes=0|host_access=NO|real_orders=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
