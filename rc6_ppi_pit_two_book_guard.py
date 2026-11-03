"""A619: OFFHOST-only two-book/decision diagnostic for native PPI journals.

This is deliberately NOT a fill authority or an order simulator. A locally
rehashable SQLite decision snapshot and PPI journal chain do *not* authenticate
their external provenance or cryptographically sign the original decision.
Unknown execution units, tariffs, depth participation, and family-specific
settlement keep every apparent opportunity NO_VERIFICADO.

The only accepted inputs are OFFHOST copies. This module never makes HTTP
requests, opens an order route, or mutates the database, journal, or runtime.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
from typing import Any
from urllib.parse import quote as urlquote

from rc6_ppi_pit_replay_bridge import NINE_FAMILY_GROUPS, causal_book_witness
from rc6_ppi_scanner_provenance import _aware, verified_native_book_journal


SCHEMA = "rc6.ppi-pit-two-book-offhost-diagnostic.v1"
DECISION_SCHEMA = "rc6.decision-inputs.v1"
MAX_SNAPSHOT_BYTES = 2_000_000


def _base(family_group: str, decision_key: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "family_group": family_group,
        "decision_key": decision_key,
        "state": "NO_VERIFICADO",
        "reason": "PIT_INPUT_EVIDENCE_UNAVAILABLE",
        "book1_native_at": None,
        "book1_observed_at": None,
        "book1_event_sha256": None,
        "book2_native_at": None,
        "book2_observed_at": None,
        "book2_event_sha256": None,
        "journal_chain_tip_sha256": None,
        "decision_snapshot_sha256": None,
        "decision_snapshot_digest_verified": False,
        "decision_signature_verified": False,
        "decision_origin_independently_verified": False,
        "source_artifact_independently_verified": False,
        "book1_asof_quote_bound": False,
        "book2_strictly_future": False,
        "book2_first_top_positive": False,
        "two_book_clock_witness": False,
        "quantity_unit_verified": False,
        "price_unit_verified": False,
        "tariff_contract_verified": False,
        "family_ready": False,
        "fill_accredited": False,
        "provider_gets": 0,
        "real_orders_sent": 0,
    }


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def _snapshot(db_path: Path, decision_key: str) -> tuple[dict[str, Any] | None, str | None, str]:
    """Read a single immutable-copy snapshot, never promote its digest to a signature."""
    try:
        file_info = db_path.lstat()
        if not stat.S_ISREG(file_info.st_mode) or stat.S_ISLNK(file_info.st_mode):
            return None, None, "DECISION_DB_FILE_UNTRUSTED"
        # Percent-encode URI metacharacters in local paths (including ? and #).
        uri = "file:" + urlquote(str(db_path.absolute()), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT payload_json,payload_sha256,schema_version "
                "FROM decision_evidence_snapshots WHERE decision_key=? LIMIT 2",
                (decision_key,),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return None, None, "DECISION_DB_UNAVAILABLE"
    if len(rows) != 1:
        return None, None, "DECISION_SNAPSHOT_MISSING_OR_AMBIGUOUS"
    raw, claimed_hash, schema = rows[0]
    if (not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_SNAPSHOT_BYTES
            or schema != DECISION_SCHEMA or not isinstance(claimed_hash, str)):
        return None, None, "DECISION_SNAPSHOT_SCHEMA_OR_CAPACITY_INVALID"
    actual_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if actual_hash != claimed_hash:
        return None, None, "DECISION_SNAPSHOT_DIGEST_MISMATCH"
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None, None, "DECISION_SNAPSHOT_JSON_INVALID"
    if not isinstance(payload, dict):
        return None, None, "DECISION_SNAPSHOT_SHAPE_INVALID"
    return payload, actual_hash, ""


def _book1_matches_quote(event: dict[str, Any], quote: dict[str, Any]) -> bool:
    """Bind the exact in-memory native quote to its predecision Book1 event."""
    if (quote.get("observed_at") != event["known_at"]
            or quote.get("book_at") != event["native_book_at"]):
        return False
    for side, price, quantity in (("bids", "bid", "bid_size"),
                                  ("offers", "ask", "ask_size")):
        levels = event["book"][side]
        native_price = _decimal(levels[0]["price"]) if levels else Decimal(0)
        native_quantity = _decimal(levels[0]["quantity"]) if levels else Decimal(0)
        if (native_price is None or native_quantity is None
                or _decimal(quote.get(price)) != native_price
                or _decimal(quote.get(quantity)) != native_quantity):
            return False
    return True


def diagnose_two_book_pit(
    journal_path: Path,
    decision_db_path: Path,
    *,
    decision_key: str,
    family_group: str,
    identity: tuple[str, str, str, str, str],
    latency_seconds: float,
    max_book1_age_seconds: float = 120.0,
) -> dict[str, Any]:
    """Explain an as-of decision + first future PPI Book2, without ever filling.

    A valid clock witness is *necessary but not sufficient* for a PAPER fill.
    The amount, charges, executable settlement, producer authentication and
    independent decision signature remain absent and cannot be caller asserted.
    FCI is NAV/cutoff-based, never an order-book IOC market fill.
    """
    result = _base(family_group, decision_key)
    if (family_group not in NINE_FAMILY_GROUPS
            or not isinstance(decision_key, str) or not 0 < len(decision_key) <= 1024
            or not isinstance(identity, (list, tuple)) or len(identity) != 5
            or any(not isinstance(part, str) or not part.strip() for part in identity)
            or identity[1] != family_group
            or isinstance(latency_seconds, bool) or not isinstance(latency_seconds, (int, float))
            or not 0 <= latency_seconds <= 300
            or isinstance(max_book1_age_seconds, bool)
            or not isinstance(max_book1_age_seconds, (int, float))
            or not 0 <= max_book1_age_seconds <= 900):
        return {**result, "state": "BLOQUEADO", "reason": "PIT_TWO_BOOK_INPUT_INVALID"}

    # A fund has no executable PPI order-book crossing. This negative guard
    # must remain even if someone supplies a plausible-looking FCI bid/ask.
    if family_group == "FCI":
        return {**result, "reason": "FCI_NAV_CUTOFF_SETTLEMENT_AUTHORITY_REQUIRED"}

    payload, digest, error = _snapshot(Path(decision_db_path), decision_key)
    if error:
        return {**result, "state": "BLOQUEADO" if "INVALID" in error or "MISMATCH" in error
                or "UNTRUSTED" in error else "NO_VERIFICADO", "reason": error}
    result["decision_snapshot_sha256"] = digest
    result["decision_snapshot_digest_verified"] = True
    decision = payload.get("decision")
    used = payload.get("quote_used")
    runtime = payload.get("runtime")
    if (payload.get("schema") != DECISION_SCHEMA
            or payload.get("decision_key") != decision_key
            or payload.get("capture_phase") not in {"NATIVE_DECISION", "ATOMIC_PAPER_ADMISSION"}
            or not isinstance(decision, dict) or not isinstance(used, dict)
            or not isinstance(runtime, dict)
            or runtime.get("execution_mode") != "PRODUCTION_PAPER_SIMULATED"
            or runtime.get("real_money_authorized") is not False
            or runtime.get("real_routes_used", []) != []
            or decision.get("symbol") != identity[0]
            or decision.get("action") not in {"BUY", "SELL"}
            or tuple(used.get(name) for name in
                     ("symbol", "asset_class", "market", "currency", "settlement")) != tuple(identity)):
        return {**result, "state": "BLOQUEADO", "reason": "DECISION_SNAPSHOT_IDENTITY_OR_MODE_INVALID"}
    decision_at = payload.get("decision_at")
    if (_aware(decision_at) is None or runtime.get("decision_at") != decision_at
            or _aware(used.get("observed_at")) is None
            or _aware(used["observed_at"]) > _aware(decision_at)):
        return {**result, "state": "BLOQUEADO", "reason": "DECISION_CAUSAL_CLOCK_UNATTESTED"}

    rows, verified = verified_native_book_journal(Path(journal_path))
    result["journal_chain_tip_sha256"] = verified.get("latest_sha256")
    if verified.get("state") != "HASH_CHAIN_VERIFIED":
        return {**result, "state": ("BLOQUEADO" if verified.get("state") == "HOLD"
                  else "NO_VERIFICADO"), "reason": verified.get("reason", "PPI_BOOK_UNAVAILABLE")}
    at = _aware(decision_at)
    prior = [row for row in rows if tuple(row["canonical_identity"]) == tuple(identity)
             and _aware(row["native_book_at"]) <= at and _aware(row["known_at"]) <= at]
    if not prior:
        return {**result, "reason": "PPI_BOOK1_KNOWN_ASOF_DECISION_ABSENT"}
    # The last known Book1, not an arbitrarily convenient earlier level.
    book1 = prior[-1]
    result.update(book1_native_at=book1["native_book_at"],
                  book1_observed_at=book1["known_at"],
                  book1_event_sha256=book1["event_sha256"])
    if ((at - _aware(book1["native_book_at"])).total_seconds() > max_book1_age_seconds):
        return {**result, "reason": "PPI_BOOK1_STALE_AT_DECISION"}
    if not _book1_matches_quote(book1, used):
        return {**result, "state": "BLOQUEADO", "reason": "PPI_BOOK1_DECISION_QUOTE_MISMATCH"}
    result["book1_asof_quote_bound"] = True

    book2 = causal_book_witness(
        Path(journal_path), family_group=family_group, identity=tuple(identity),
        decision_at=decision_at, latency_seconds=latency_seconds,
        side=decision["action"], decision_receipt_sha256=digest,
    )
    if book2.get("journal_chain_tip_sha256") != verified.get("latest_sha256"):
        return {**result, "state": "BLOQUEADO", "reason": "PPI_JOURNAL_CHANGED_BETWEEN_READS"}
    if book2.get("state") == "BLOQUEADO":
        return {**result, "state": "BLOQUEADO", "reason": book2.get("reason")}
    if not book2.get("causal_clock_witness"):
        return {**result, "reason": book2.get("reason", "PPI_BOOK2_FUTURE_ABSENT")}
    result.update(book2_native_at=book2["first_book_native_at"],
                  book2_observed_at=book2["first_book_observed_at"],
                  book2_event_sha256=book2["journal_event_sha256"],
                  book2_strictly_future=True,
                  book2_first_top_positive=bool(book2["first_book_has_positive_native_top_level"]))
    # A next-session Book is not an executable continuation of an intraday
    # crossing, even when its native timestamp is chronologically later.
    future = next((row for row in rows if row["event_sha256"] == result["book2_event_sha256"]), None)
    if (future is None or future["session_art"] != book1["session_art"]
            or _aware(future["native_book_at"]) <= _aware(book1["native_book_at"])):
        return {**result, "state": "BLOQUEADO", "reason": "PPI_BOOK2_CROSS_SESSION_OR_ORDER_INVALID"}
    if not result["book2_first_top_positive"]:
        return {**result, "reason": "PPI_BOOK2_FIRST_TOP_DEPTH_ZERO_OR_ABSENT"}

    result["two_book_clock_witness"] = True
    return {**result, "reason": "PIT_TWO_BOOK_WITNESS_ONLY_NOT_AN_AUTHENTICATED_FILL"}


def diagnostics_json(*args: Any, **kwargs: Any) -> str:
    """Deterministic, receipt-sized output for an OFFHOST artifact audit."""
    return json.dumps(diagnose_two_book_pit(*args, **kwargs), sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    """OFFHOST CLI for copies exported by controlled GitHub Actions, no PPI GET."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--decision-db", type=Path, required=True)
    parser.add_argument("--decision-key", required=True)
    parser.add_argument("--family", choices=NINE_FAMILY_GROUPS, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--market", required=True)
    parser.add_argument("--currency", required=True)
    parser.add_argument("--settlement", required=True)
    parser.add_argument("--latency", type=float, required=True)
    parser.add_argument("--max-book1-age", type=float, default=120)
    args = parser.parse_args()
    print(diagnostics_json(
        args.journal, args.decision_db,
        decision_key=args.decision_key, family_group=args.family,
        identity=(args.symbol, args.family, args.market, args.currency,
                  args.settlement), latency_seconds=args.latency,
        max_book1_age_seconds=args.max_book1_age,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
