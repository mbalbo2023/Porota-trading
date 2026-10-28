"""Pure source precedence for existing caches; never a provider transport.

Receipt/capture proves availability. Native event/publication clocks prove age.
Complementary claims remain evidence: they cannot replace the primary identity
or hide disagreement. The resolver does not authorize an entry or a fill.
"""
from collections.abc import Mapping
import math

from rc6_dynamic_universe.common import digest, stamp

VERSION = "RC6_SOURCE_AUTHORITY_V1"
PRIMARY_SOURCES = frozenset({
    "PPI", "PPI_API", "PPI_CATALOG", "PPI_STRUCTURED_API", "PPI_WEB",
    "PPI_AUTHENTICATED_WEB", "PPI_MARKETDATA", "PPI_MARKETDATA_CURRENT",
    "PPI_MARKETDATA_BOOK", "PPI_MARKETDATA_INTRADAY", "PPI_INTRADAY",
    "PPI_SEARCH_INSTRUMENT_DESCRIPTION", "PPI_SEARCH_INSTRUMENT",
    "PPI_OPTION_CHAIN", "PPI_OPTIONCHAIN",
    "PPI_SQLITE_MARKET_SNAPSHOTS",
})
NATIVE_TIMES = ("source_at", "provider_observed_at", "provider_timestamp", "timestamp", "trade_at", "tradeDate", "event_at", "date")
RECEIPT_TIMES = ("received_at", "captured_at", "capture_observed_at", "observed_at", "collected_at", "refreshed_at")
MAX_FIELD_EVIDENCE = 16


def source_rank(source):
    parts = str(source or "").strip().upper().split("+")
    # Composite contract metadata may mention PPI identity while its field came
    # from IOL/official analytics. Only a pure, explicit PPI field origin ranks
    # as primary. Field envelopes can identify that origin independently.
    if all(part in PRIMARY_SOURCES for part in parts):
        return 0
    if any(part == "IOL" or part.startswith("IOL_") for part in parts):
        return 1
    if any(part in {"BYMA", "BYMA_PUBLIC", "BYMA_PUBLIC_SCRAPER"} for part in parts):
        return 2
    return 3


def authority(source):
    return ("PPI_PRIMARY", "IOL_COMPLEMENT", "BYMA_PUBLIC_OBSERVE_ONLY", "OTHER_COMPLEMENT")[source_rank(source)]


def _time(value):
    try:
        return stamp(value) if value not in (None, "") else None
    except (TypeError, ValueError, OverflowError):
        return None


def native_time(row):
    """No receipt/capture/publication label can stand in for a quote event."""
    return next((row[key] for key in NATIVE_TIMES if row.get(key) not in (None, "")), None)


def receipt_time(row):
    return next((row[key] for key in RECEIPT_TIMES if row.get(key) not in (None, "")), None)


def _same(left, right, tolerance):
    if left.get("unit") and right.get("unit") and left["unit"] != right["unit"]:
        return False
    a, b = left.get("value"), right.get("value")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(a, b, rel_tol=tolerance, abs_tol=1e-12)
    return a == b


def resolve_field(candidates, *, as_of, ttl_seconds=120, static=False, tolerance_fraction=0):
    """Latest evidence per source-path, then primary/complement precedence.

    Same-clock contradictory revisions are review conflicts, never arbitrary
    picks. The latest invalid/stale record invalidates older values on its path.
    Future receipts are not available at the cutoff. All diagnostics are bounded.
    Static inputs may prove availability without a native publication clock;
    that clock stays None and cannot become a quote/event freshness claim.
    """
    at = stamp(as_of)
    latest, unavailable, overflowing_paths = {}, [], set()
    input_count = 0
    for raw in candidates:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        input_count += 1
        source = str(row.get("source") or "")
        path = str(row.get("source_path") or source)
        scope = (source, path)
        declared_native = row.get("source_at") not in (None, "")
        received, native = _time(row.get("received_at")), _time(row.get("source_at"))
        row = {"value": row.get("value"), "source": source,
               "source_path": path, "authority": authority(source),
               "source_at": native.isoformat() if native else None,
               "received_at": received.isoformat() if received else None,
               "unit": row.get("unit"), "valid": row.get("valid", True) is True,
               "field_origin_status": "COMPOSITE_COMPLEMENT_ORIGIN" if "+" in source and source_rank(source) != 0 else "EXPLICIT_SOURCE_ORIGIN",
               "native_reason": row.get("native_reason")}
        if not received or received > at:
            row["rejection_reason"] = "NOT_AVAILABLE_AT_CUTOFF" if received else "RECEIPT_CLOCK_MISSING"
            unavailable.append(row)
            continue
        static_available = bool(static and not native and not declared_native and source and row["valid"] and row["value"] is not None)
        if static_available and ttl_seconds is not None and (at-received).total_seconds() > ttl_seconds:
            reason = "STATIC_EVIDENCE_AVAILABILITY_EXPIRED"
        elif static_available:
            reason = None
        elif not native:
            reason = "PROVIDER_TIMESTAMP_MISSING_OR_AMBIGUOUS"
        elif native > received:
            reason = "SOURCE_RECEIPT_CLOCK_MISMATCH"
        elif ttl_seconds is not None and (at-native).total_seconds() > ttl_seconds:
            reason = "STALE_QUOTES"
        elif not source or not row["valid"] or row["value"] is None:
            reason = row["native_reason"] or "SOURCE_UNAVAILABLE"
        else:
            reason = None
        row["rejection_reason"] = reason
        old = latest.get(scope, [])
        if not old or received > stamp(old[0]["received_at"]):
            latest[scope] = [row]
            overflowing_paths.discard(scope)
        elif received == stamp(old[0]["received_at"]) and digest(row) not in {digest(item) for item in old}:
            if len(old) < MAX_FIELD_EVIDENCE:
                latest[scope].append(row)
            else:
                overflowing_paths.add(scope)
    active, conflicts, rejected = [], [], []
    for scope, rows in sorted(latest.items()):
        distinct = {digest({k: row[k] for k in ("value", "unit", "valid", "source_at", "rejection_reason")}) for row in rows}
        if len(distinct) > 1 or scope in overflowing_paths:
            conflicts.append({"reason": "DUPLICATE_SOURCE_EVIDENCE_CONFLICT", "source": scope[0], "source_path": scope[1],
                              "evidence": rows[:MAX_FIELD_EVIDENCE]})
            # No value wins a same-clock conflict from this path.
            rejected.extend(rows)
        elif rows[0]["rejection_reason"]:
            rejected.append(rows[0])
        else:
            active.append(rows[0])
    active.sort(key=lambda r: (source_rank(r["source"]), r["source_path"]))
    chosen = active[0] if active else None
    if chosen:
        for candidate in active[1:]:
            if not _same(chosen, candidate, tolerance_fraction):
                conflicts.append({"reason": "SOURCE_FIELD_DISCREPANCY_REVIEW_REQUIRED",
                    "primary": chosen, "complement": candidate})
    result = {"value": chosen["value"] if chosen else None,
        "status": ("VERIFIED_STATIC_INPUT" if static else "OBSERVED_FRESH") if chosen else "NO_VERIFICADO",
        "authority_policy": VERSION, "review_required": bool(conflicts),
        "conflicts": conflicts[:MAX_FIELD_EVIDENCE],
        "candidates": (active + rejected + unavailable)[:MAX_FIELD_EVIDENCE],
        "candidate_count": len(active) + len(rejected) + len(unavailable),
        "input_count": input_count,
        "evidence_truncated": bool(overflowing_paths) or len(active) + len(rejected) + len(unavailable) > MAX_FIELD_EVIDENCE,
        "provider_state": "NO_VERIFICADO", "entry_authority": False}
    if chosen:
        result.update({k: chosen[k] for k in ("source", "source_path", "authority", "source_at", "received_at", "unit", "field_origin_status")})
        result["age_seconds"] = (at-stamp(chosen["source_at"])).total_seconds() if chosen["source_at"] else None
        result["native_clock_status"] = "EXPLICIT_NATIVE_TIME" if chosen["source_at"] else "NO_VERIFICADO"
        result["availability_age_seconds"] = (at-stamp(chosen["received_at"])).total_seconds()
    if conflicts:
        result["review_status"] = "CONFLICT_REVIEW_REQUIRED"
    return result
