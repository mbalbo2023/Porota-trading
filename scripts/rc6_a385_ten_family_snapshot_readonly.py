#!/usr/bin/env python3
"""A385: bounded, non-invasive ten-family census of an ALREADY published JSON.

No SQLite, provider SDK, order route, docker, service or application module is
imported.  This is not an instrument-admission verifier: a full-universe source
export can only corroborate SOURCE/CONTRACT inventory, not PIT, fees, risk,
PAPER fills or the productive version.  Raw instruments never leave the host.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


SOURCE_PATH = Path("/opt/porota-trading/data/market/rc6_instrument_evidence_latest.json")
SCHEMA = "rc6-full-universe-instrument-evidence-v1"
RECEIPT_SCHEMA = "rc6.a385.ten-family-source-export-readonly.v1"
FAMILIES = (
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
)
SOURCES = ("PPI", "IOL", "BYMA", "A3", "A3_CEM", "CAFCI", "CNV", "ROFEX")
MAX_BYTES = 48 * 1024 * 1024
MAX_INSTRUMENTS = 60_000
MAX_AGE_SECONDS = 15 * 60  # Published every 5m; tolerate two missed cycles.
MAX_FUTURE_SKEW_SECONDS = 30
SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,55}(?::[A-Z][A-Z0-9_]{0,55})?$")
MARKET_ALIASES = {"BCBA": "BYMA", "BYMA": "BYMA", "ROFEX": "A3", "A3": "A3"}
SETTLEMENT_ALIASES = {
    "T1": "A-24HS", "T+1": "A-24HS", "24HS": "A-24HS", "A-24HS": "A-24HS",
    "T0": "INMEDIATA", "T+0": "INMEDIATA", "CI": "INMEDIATA",
    "INMEDIATA": "INMEDIATA",
}


class EvidenceRejected(Exception):
    """Strict allowlisted failure code; never embed private data or paths."""


def _utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise EvidenceRejected("GENERATION_CLOCK_MISSING")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("naive")
        return timestamp.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise EvidenceRejected("GENERATION_CLOCK_INVALID") from exc


def _strict_nonnegative(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_INSTRUMENTS


def _identity_semantically_matches(row: dict, key: list[str]) -> bool:
    """Validate ordered producer five-key without guessing missing fields.

    A source missing its canonical key is counted separately; a producer that
    claims an exact key with wrong ordering or contradictory fields is a census
    integrity failure, never a reason to guess another instrument identity.
    """
    symbol = str(row.get("symbol") or "").strip().upper()
    family = str(row.get("family") or "").strip().upper()
    if key[0] != symbol or key[1] != family:
        return False
    for offset, field, aliases in (
        (2, "market", MARKET_ALIASES),
        (4, "settlement", SETTLEMENT_ALIASES),
    ):
        value = row.get(field)
        if value in (None, ""):
            return False
        raw = str(value).strip().upper()
        if key[offset] != aliases.get(raw, raw):
            return False
    currency = row.get("currency")
    if currency in (None, "") or key[3] != str(currency).strip().upper():
        return False
    return True


def _safe_reason(value: object) -> str:
    if type(value) is str and SAFE_CODE.fullmatch(value):
        return value
    return "UNCLASSIFIED_REASON"


def _safe_open_file(path: Path, *, max_bytes: int) -> tuple[bytes, os.stat_result]:
    """Walk all directory components without following symlinks or FIFO hangs.

    The open fd anchors the data even when the producer atomically replaces its
    snapshot.  It does not read DB or create .db-wal/.db-shm sidecars.
    """
    if not path.is_absolute() or not path.name or ".." in path.parts:
        raise EvidenceRejected("SNAPSHOT_PATH_UNSAFE")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    dir_flags = flags | os.O_DIRECTORY
    folder = None
    fd = None
    try:
        folder = os.open("/", dir_flags)
        for part in path.parts[1:-1]:
            nxt = os.open(part, dir_flags, dir_fd=folder)
            os.close(folder)
            folder = nxt
        fd = os.open(path.name, flags | os.O_NONBLOCK, dir_fd=folder)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise EvidenceRejected("SNAPSHOT_FILE_UNSAFE")
        if not 0 < before.st_size <= max_bytes:
            raise EvidenceRejected("SNAPSHOT_BYTES_OUT_OF_BOUND")
        remaining = max_bytes + 1
        chunks = []
        while remaining:
            chunk = os.read(fd, min(256 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or len(raw) > max_bytes or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise EvidenceRejected("SNAPSHOT_CHANGED_OR_TRUNCATED")
        return raw, before
    except FileNotFoundError as exc:
        raise EvidenceRejected("SNAPSHOT_NOT_PUBLISHED") from exc
    except NotADirectoryError as exc:
        raise EvidenceRejected("SNAPSHOT_PATH_UNSAFE") from exc
    except (PermissionError, OSError) as exc:
        raise EvidenceRejected("SNAPSHOT_OPEN_UNAVAILABLE") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if folder is not None:
            os.close(folder)


def _empty_family(name: str) -> dict:
    return {
        "family": name,
        "instruments": 0,
        "source_contract_ready": 0,
        "source_contract_not_ready": 0,
        "identity_unresolved": 0,
        "source_selection_absent": 0,
        "first_known_at_not_exported": 0,
        "reason_counts": {},
        "eligible_source_counts": {name: 0 for name in SOURCES},
        "status": "NO_FAMILY_SAMPLE_IN_EXPORT",
    }


def _family_bucket(label: str, instruments: list[dict]) -> dict:
    result = _empty_family(label)
    reasons = Counter()
    sources = Counter()
    for row in instruments:
        result["instruments"] += 1
        ready = row.get("paper_contract_ready") is True
        result["source_contract_ready" if ready else "source_contract_not_ready"] += 1
        key = row.get("canonical_identity")
        if not isinstance(key, list) or len(key) != 5 or any(
            type(part) is not str or not part.strip() for part in key
        ):
            result["identity_unresolved"] += 1
        available = False
        raw_sources = row.get("sources")
        if isinstance(raw_sources, dict):
            for source in SOURCES:
                detail = raw_sources.get(source)
                if isinstance(detail, dict) and detail.get("selection_eligible") is True:
                    available = True
                    sources[source] += 1
        if not available:
            result["source_selection_absent"] += 1
        if not row.get("first_known_at"):
            result["first_known_at_not_exported"] += 1
        reasons[_safe_reason(row.get("reason"))] += 1
    top_reasons = sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:16]
    omitted = sum(reasons.values()) - sum(value for _, value in top_reasons)
    result["reason_counts"] = dict(sorted(top_reasons))
    if omitted:
        result["reason_counts"]["OTHER_REASON_CODES"] = omitted
    result["eligible_source_counts"] = {source: sources[source] for source in SOURCES}
    if instruments:
        result["status"] = (
            "ALL_SOURCE_CONTRACT_READY" if result["source_contract_ready"] == len(instruments)
            else "SOME_SOURCE_CONTRACT_READY" if result["source_contract_ready"]
            else "NO_SOURCE_CONTRACT_READY"
        )
    return result


def _validate_and_summarize(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise EvidenceRejected("SNAPSHOT_SCHEMA_INVALID")
    universe = payload.get("universe")
    instruments = payload.get("instruments")
    family_reported = payload.get("families")
    counters = payload.get("counts")
    if not all(isinstance(x, (dict, list)) for x in (universe, instruments, family_reported, counters)):
        raise EvidenceRejected("SNAPSHOT_SHAPE_INVALID")
    if not isinstance(universe, dict) or not isinstance(instruments, list) or not isinstance(family_reported, list) or not isinstance(counters, dict):
        raise EvidenceRejected("SNAPSHOT_SHAPE_INVALID")
    expected = universe.get("catalog_count")
    actual = universe.get("ledger_count")
    if (not _strict_nonnegative(expected) or not _strict_nonnegative(actual)
            or not 0 < expected == actual == len(instruments) <= MAX_INSTRUMENTS
            or universe.get("ledger_complete") is not True):
        raise EvidenceRejected("UNIVERSE_DENOMINATOR_UNVERIFIED")
    if payload.get("decision_effect") != "OBSERVE_ONLY" or payload.get("real_money_authorized") is not False:
        raise EvidenceRejected("PAPER_READONLY_INVARIANT_UNVERIFIED")
    if counters.get("paper_auto_enabled") != 0:
        raise EvidenceRejected("PAPER_ENTRY_AUTHORITY_UNVERIFIED")
    if not isinstance(payload.get("source_order"), str) or not payload["source_order"].startswith("PPI_PRIMARY"):
        raise EvidenceRejected("PPI_IDENTITY_AUTHORITY_UNVERIFIED")
    grouped = {family: [] for family in FAMILIES}
    unmapped = 0
    ready_total = 0
    exact_keys = set()
    duplicate_exact = 0
    semantic_mismatches = 0
    for row in instruments:
        if not isinstance(row, dict) or row.get("paper_auto_enabled") is not False or row.get("entry_authority") is not False or row.get("real_money_authorized") is not False:
            raise EvidenceRejected("INSTRUMENT_PAPER_GUARD_UNVERIFIED")
        if type(row.get("paper_contract_ready")) is not bool:
            raise EvidenceRejected("INSTRUMENT_READY_TYPE_INVALID")
        ready_total += bool(row["paper_contract_ready"])
        family = row.get("family")
        if type(family) is str and family in grouped:
            grouped[family].append(row)
        else:
            unmapped += 1
        key = row.get("canonical_identity")
        if isinstance(key, list) and len(key) == 5 and all(type(part) is str and part.strip() for part in key):
            tup = tuple(key)
            if tup in exact_keys:
                duplicate_exact += 1
            exact_keys.add(tup)
            if not _identity_semantically_matches(row, key):
                semantic_mismatches += 1
    if counters.get("paper_contract_ready") != ready_total:
        raise EvidenceRejected("GLOBAL_READY_COUNT_MISMATCH")
    report_lookup = {}
    for item in family_reported:
        if not isinstance(item, dict) or type(item.get("family")) is not str:
            raise EvidenceRejected("FAMILY_REPORT_SHAPE_INVALID")
        name = item["family"]
        if name in report_lookup:
            raise EvidenceRejected("FAMILY_REPORT_DUPLICATE")
        report_lookup[name] = item
    families = []
    for family in FAMILIES:
        bucket = _family_bucket(family, grouped[family])
        source = report_lookup.get(family)
        if bucket["instruments"] and not isinstance(source, dict):
            raise EvidenceRejected("FAMILY_REPORT_MISSING")
        if source and (source.get("instrument_count") != bucket["instruments"] or
                       source.get("paper_contract_ready") != bucket["source_contract_ready"] or
                       source.get("paper_auto_enabled") != 0):
            raise EvidenceRejected("FAMILY_REPORTED_COUNT_MISMATCH")
        families.append(bucket)
    if sum(x["instruments"] for x in families) + unmapped != actual:
        raise EvidenceRejected("FAMILY_DENOMINATOR_MISMATCH")
    return {
        "universe_instruments": actual,
        "mapped_family_instruments": actual - unmapped,
        "unmapped_family_instruments": unmapped,
        "duplicate_exact_identity_count": duplicate_exact,
        "identity_semantic_mismatch_count": semantic_mismatches,
        "source_contract_ready_in_export": ready_total,
        "families_present": sum(bool(x["instruments"]) for x in families),
        "families_expected": len(FAMILIES),
        "families": families,
    }


def inspect_snapshot(path: Path = SOURCE_PATH, *, current: datetime | None = None,
                     max_bytes: int = MAX_BYTES) -> dict:
    current = current or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("current must be timezone aware")
    current = current.astimezone(timezone.utc)
    report = {
        "schema": RECEIPT_SCHEMA,
        "read_only": True,
        "source": "EXISTING_FULL_UNIVERSE_JSON_ONLY",
        "db_opened": False,
        "provider_calls": 0,
        "order_routes_called": False,
        "admission_authority": False,
        "causal_pit_receipt_validated": False,
        "fees_risk_and_fill_lineage_validated": False,
        "runtime_sha_verified": False,
        "status": "NO_VERIFICADO",
        "reason": "SNAPSHOT_NOT_INSPECTED",
        "observed_at_utc": current.isoformat(),
    }
    try:
        raw, info = _safe_open_file(path, max_bytes=max_bytes)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise EvidenceRejected("SNAPSHOT_JSON_INVALID") from exc
        generated = _utc_timestamp(payload.get("generated_at") if isinstance(payload, dict) else None)
        elapsed = (current - generated).total_seconds()
        published = datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)
        file_elapsed = (current - published).total_seconds()
        if (elapsed < -MAX_FUTURE_SKEW_SECONDS or file_elapsed < -MAX_FUTURE_SKEW_SECONDS or
                generated - published > timedelta(seconds=120) or
                published - generated > timedelta(seconds=120)):
            raise EvidenceRejected("SNAPSHOT_CLOCK_INCONSISTENT")
        summary = _validate_and_summarize(payload)
        report.update({
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_bytes": len(raw),
            "source_generated_at_utc": generated.isoformat(),
            "source_file_mtime_utc": published.isoformat(),
            "age_seconds": round(elapsed, 1),
            **summary,
        })
        if elapsed > MAX_AGE_SECONDS or file_elapsed > MAX_AGE_SECONDS:
            report["reason"] = "SNAPSHOT_STALE"
        elif (summary["unmapped_family_instruments"] or
              summary["duplicate_exact_identity_count"] or
              summary["identity_semantic_mismatch_count"]):
            report["reason"] = "IDENTITY_CENSUS_REVIEW_REQUIRED"
        elif summary["families_present"] < len(FAMILIES):
            report["reason"] = "FAMILY_COVERAGE_INCOMPLETE"
        else:
            report["status"] = "VALID_SOURCE_EXPORT_ONLY"
            report["reason"] = "FRESH_FULL_UNIVERSE_AGGREGATES"
    except EvidenceRejected as exc:
        report["reason"] = str(exc)
    return report


def main(args: list[str] | None = None) -> int:
    args = sys.argv[1:] if args is None else args
    if args != ["host-probe"]:
        print("Usage: rc6_a385_ten_family_snapshot_readonly.py host-probe", file=sys.stderr)
        return 64
    receipt = inspect_snapshot()
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if receipt["status"] == "VALID_SOURCE_EXPORT_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
