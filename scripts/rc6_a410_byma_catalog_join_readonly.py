#!/usr/bin/env python3
"""A410: privacy-safe, bounded BYMA/export identity JOIN diagnostic.

No provider calls, database connections, filesystem writes or order routes.
Only kernel MemAvailable and two published JSON files are read. Raw symbols, quotes,
timestamps and source paths never leave the host.  This cannot grant PAPER
readiness, source freshness, PIT, margin or fee authority.

Alias semantics are pinned to rc6_dynamic_universe/sources.py on the existing
product candidate 4e3070492772c02ad51268aacafacd344c7f4da9.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import re
import resource
import stat
import sys


SCHEMA = "rc6.a410.byma-catalog-join-readonly.v2"
CATALOG_SCHEMA = "rc6-full-universe-instrument-evidence-v1"
BYMA_SCHEMA = "rc6-consolidated-source-evidence-v1"
CATALOG_PATH = Path("/opt/porota-trading/data/market/rc6_instrument_evidence_latest.json")
BYMA_PATH = Path("/opt/porota-trading/data/market/rc6_public_sources_latest.json")
MAX_CATALOG_BYTES = 48 * 1024 * 1024
MAX_BYMA_BYTES = 12 * 1024 * 1024
# RLIMIT_AS caps process virtual address space, and therefore its resident set,
# before any provider JSON is read. Breaching this cap is a negative diagnosis.
MAX_ADDRESS_SPACE_BYTES = 256 * 1024 * 1024
# A fixed 128 MiB host reserve above the process cap prevents diagnostics
# from starting during low-memory conditions; no host RAM claim if unknown.
MIN_HOST_MEMAVAILABLE_BYTES = MAX_ADDRESS_SPACE_BYTES + 128 * 1024 * 1024
MAX_MEMINFO_BYTES = 16 * 1024
MAX_ROWS = 60_000
FAMILIES = {
    "ACCION": "ACCIONES", "ACCIONES": "ACCIONES",
    "CEDEAR": "CEDEARS", "CEDEARS": "CEDEARS",
    "ETF": "ETFS", "ETFS": "ETFS", "BONO": "BONOS",
    "BONOS": "BONOS", "TIT. PUBLICOS": "BONOS",
    "LETRA": "LETRAS", "LETRAS": "LETRAS",
    "ON": "OBLIGACIONES", "OBL.NEG.": "OBLIGACIONES",
    "OBL.PYME": "OBLIGACIONES", "OBLIGACIONES": "OBLIGACIONES",
    "OBLIGACIONES_NEGOCIABLES": "OBLIGACIONES",
    "OPCION": "OPCIONES", "OPCIONES": "OPCIONES",
    "FUTURO": "FUTUROS", "FUTUROS": "FUTUROS",
    "CAUCION": "CAUCIONES", "CAUCIONES": "CAUCIONES",
    "FCI": "FCI", "FCI_LOCAL": "FCI", "FCI LOCAL": "FCI",
}
MARKETS = {"BCBA": "BYMA", "BYMA": "BYMA", "ROFEX": "A3", "A3": "A3"}
TERMS = {
    "T1": "A-24HS", "T+1": "A-24HS", "24HS": "A-24HS", "A-24HS": "A-24HS",
    "T0": "INMEDIATA", "T+0": "INMEDIATA", "CI": "INMEDIATA",
    "INMEDIATA": "INMEDIATA",
}
CURRENCIES = frozenset({"ARS", "USD", "USD_MEP", "USD_CCL"})
CANONICAL_FAMILIES = tuple(dict.fromkeys(FAMILIES.values()))
NATIVE_CLOCKS = ("provider_observed_at", "provider_timestamp",
                 "source_at", "timestamp", "trade_at", "event_at",
                 "date", "tradeDate")
RECEIPT_CLOCKS = ("received_at", "captured_at", "capture_observed_at",
                  "observed_at", "collected_at", "refreshed_at", "generated_at")
COUNT_KEYS = (
    "catalog_rows", "catalog_exact_keys", "catalog_missing_identity",
    "catalog_duplicate_identity", "byma_rows", "byma_key_valid",
    "byma_key_invalid", "byma_unique_keys", "byma_exact_rows",
    "byma_exact_unique", "byma_unmatched_key", "byma_symbol_absent",
    "byma_same_symbol_different_key", "byma_multiple_keys_same_symbol",
    "different_family", "different_market", "different_currency",
    "different_settlement", "byma_native_clock_present",
    "byma_native_clock_aware", "byma_receipt_clock_present",
    "byma_nested_quote_rows",
)
REJECT_CODES = (
    "MISSING_SYMBOL", "MISSING_FAMILY", "MISSING_MARKET",
    "MISSING_CURRENCY", "MISSING_SETTLEMENT", "SYMBOL_CONFLICT",
    "FAMILY_CONFLICT", "SETTLEMENT_CONFLICT", "UNKNOWN_FAMILY",
    "UNKNOWN_MARKET", "UNKNOWN_CURRENCY", "UNKNOWN_SETTLEMENT",
    "ROW_NOT_OBJECT",
)
# A503: public aggregates only.  Never include a previously unseen provider
# code, raw alias value, ticker, quote, timestamp or sample in these enums.
# The normalized BYMA cache does not preserve the original provider field
# names, so these counts cannot identify which upstream alias was selected.
PROFILE_FIELDS = ("currency", "settlement", "term", "settlement_code")
VALUE_TYPES = (
    "ABSENT", "NULL", "BLANK_STRING", "STRING", "INTEGER", "FLOAT",
    "BOOLEAN", "OBJECT", "ARRAY",
)
CURRENCY_CLASSES = (
    "MISSING", "ARS", "USD", "USD_MEP", "USD_CCL",
    "EXT_UNMAPPED", "OTHER_ALPHA", "OTHER_NUMERIC", "OTHER_TEXT", "NONSTRING",
)
SETTLEMENT_CLASSES = (
    "MISSING", "A_24HS_CANONICAL", "INMEDIATA_CANONICAL",
    "T1_ALIAS", "T0_ALIAS", "OTHER_ALPHA", "OTHER_NUMERIC",
    "OTHER_TEXT", "NONSTRING", "ALIAS_CONFLICT",
)
_NUMERIC_CODE = re.compile(r"[+-]?[0-9]+(?:[.,][0-9]+)?\Z")
_ALPHA_CODE = re.compile(r"[A-Z]+\Z")
ERROR_CODES = frozenset({
    "FILE_MISSING", "PATH_UNSAFE", "FILE_UNSAFE", "READ_UNAVAILABLE",
    "FILE_SIZE_INVALID", "FILE_CHANGED", "JSON_INVALID",
    "CATALOG_SCHEMA_INVALID", "CATALOG_SHAPE_INVALID",
    "BYMA_SHAPE_INVALID", "ROWS_EXCEEDED", "CLOCK_INVALID",
    "PROBE_FAILED", "MEMORY_LIMIT_UNAVAILABLE",
    "HOST_MEMORY_UNKNOWN", "HOST_MEMORY_BELOW_RESERVE",
})


class DiagnosticRejected(Exception):
    """A fixed public code, never an exception with raw content."""


def _lock_address_space_limit() -> None:
    """Enforce <=256 MiB Linux RLIMIT_AS before reading/parsing host JSON.

    Preserve any stricter inherited limit; if OS enforcement cannot be verified,
    refuse the host diagnostic. No environment overrides or privilege changes.
    """
    try:
        resource_id = resource.RLIMIT_AS
        inherited = resource.getrlimit(resource_id)
        bounds = [MAX_ADDRESS_SPACE_BYTES]
        bounds.extend(value for value in inherited
                      if value != resource.RLIM_INFINITY)
        limit = min(bounds)
        if not 0 < limit <= MAX_ADDRESS_SPACE_BYTES:
            raise ValueError("invalid inherited address space budget")
        resource.setrlimit(resource_id, (limit, limit))
        if resource.getrlimit(resource_id) != (limit, limit):
            raise ValueError("address space budget not enforced")
    except (AttributeError, OSError, ValueError, MemoryError) as exc:
        raise DiagnosticRejected("MEMORY_LIMIT_UNAVAILABLE") from exc


def _require_host_memory_reserve() -> None:
    """Fail closed unless kernel MemAvailable leaves 128 MiB over process cap.

    /proc/meminfo is fixed kernel metadata, read with a 16 KiB upper bound;
    never log memory values, raw text, private paths or exceptions. The reserve
    is a preflight estimate, not a guarantee against concurrent processes.
    """
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            content = handle.read(MAX_MEMINFO_BYTES + 1)
        if not content or len(content) > MAX_MEMINFO_BYTES:
            raise DiagnosticRejected("HOST_MEMORY_UNKNOWN")
        fields = [line.split() for line in content.splitlines()
                  if line.startswith("MemAvailable:")]
        if (len(fields) != 1 or len(fields[0]) != 3 or
                fields[0][0] != "MemAvailable:" or fields[0][2] != "kB" or
                not fields[0][1].isdecimal()):
            raise DiagnosticRejected("HOST_MEMORY_UNKNOWN")
        available_bytes = int(fields[0][1]) * 1024
        if available_bytes < MIN_HOST_MEMAVAILABLE_BYTES:
            raise DiagnosticRejected("HOST_MEMORY_BELOW_RESERVE")
    except (OSError, UnicodeError, ValueError, MemoryError) as exc:
        raise DiagnosticRejected("HOST_MEMORY_UNKNOWN") from exc


def safe_bytes(path: Path, cap: int) -> tuple[bytes, os.stat_result]:
    """Open directories/files without following symlinks, with stable fstat."""
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise DiagnosticRejected("PATH_UNSAFE")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    folder = fd = None
    try:
        folder = os.open("/", flags | os.O_DIRECTORY)
        for component in path.parts[1:-1]:
            next_folder = os.open(component, flags | os.O_DIRECTORY, dir_fd=folder)
            os.close(folder)
            folder = next_folder
        fd = os.open(path.name, flags | os.O_NONBLOCK, dir_fd=folder)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise DiagnosticRejected("FILE_UNSAFE")
        if not 0 < before.st_size <= cap:
            raise DiagnosticRejected("FILE_SIZE_INVALID")
        remaining, parts = cap + 1, []
        while remaining:
            chunk = os.read(fd, min(256 * 1024, remaining))
            if not chunk:
                break
            parts.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(parts)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or len(raw) > cap or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                 after.st_ctime_ns)):
            raise DiagnosticRejected("FILE_CHANGED")
        return raw, before
    except FileNotFoundError as exc:
        raise DiagnosticRejected("FILE_MISSING") from exc
    except NotADirectoryError as exc:
        raise DiagnosticRejected("PATH_UNSAFE") from exc
    except (OSError, PermissionError) as exc:
        raise DiagnosticRejected("READ_UNAVAILABLE") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if folder is not None:
            os.close(folder)


def _text(value: object) -> str:
    return str(value or "").strip().upper()


def _first(row: dict, keys: tuple[str, ...], aliases: dict | None, label: str):
    values = [row[key] for key in keys if row.get(key) not in (None, "")]
    if not values:
        raise DiagnosticRejected("MISSING_" + label)
    # Never stringify an ambiguous or untyped symbol into a valid identity.
    if label == "SYMBOL" and any(
            not isinstance(value, str) or not value.strip() for value in values):
        raise DiagnosticRejected("MISSING_SYMBOL")
    present = [_text(value) for value in values]
    translated = [(aliases or {}).get(value, value) for value in present]
    if len(set(translated)) != 1:
        raise DiagnosticRejected(label + "_CONFLICT")
    if label == "SYMBOL" and translated[0] == "UNKNOWN":
        raise DiagnosticRejected("MISSING_SYMBOL")
    return translated[0]


def exact_byma_key(row: object) -> tuple[str, str, str, str, str]:
    if not isinstance(row, dict):
        raise DiagnosticRejected("ROW_NOT_OBJECT")
    # Match rc6_dynamic_universe/sources.py exactly: a nested quote is NOT
    # interpreted as a fallback source of the five identity fields.
    joined = row
    symbol = _first(joined, ("ticker", "symbol"), None, "SYMBOL")
    family = _first(joined, ("instrument_type", "family", "asset_type", "asset_class"),
                    FAMILIES, "FAMILY")
    market = _text(joined.get("market"))
    currency = _text(joined.get("currency"))
    settlement = _first(joined, ("settlement", "term", "settlement_code"),
                        TERMS, "SETTLEMENT")
    if symbol == "UNKNOWN":
        raise DiagnosticRejected("MISSING_SYMBOL")
    if not market:
        raise DiagnosticRejected("MISSING_MARKET")
    if not currency:
        raise DiagnosticRejected("MISSING_CURRENCY")
    market = MARKETS.get(market, market)
    if family not in CANONICAL_FAMILIES:
        raise DiagnosticRejected("UNKNOWN_FAMILY")
    if market not in set(MARKETS.values()):
        raise DiagnosticRejected("UNKNOWN_MARKET")
    if currency not in CURRENCIES:
        raise DiagnosticRejected("UNKNOWN_CURRENCY")
    if settlement not in set(TERMS.values()):
        raise DiagnosticRejected("UNKNOWN_SETTLEMENT")
    return symbol, family, market, currency, settlement


def _age(seconds: float) -> int:
    if seconds < -30 or seconds > 1_000_000_000:
        raise DiagnosticRejected("CLOCK_INVALID")
    return max(0, int(seconds))


def _aware_clock(row: dict, keys: tuple[str, ...]) -> bool:
    for key in keys:
        raw = row.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            continue
        if moment.tzinfo is not None and moment.utcoffset() is not None:
            return True
    return False


def _new_report():
    return {
        "schema": SCHEMA,
        "read_only": True, "db_opened": False, "provider_calls": 0,
        "order_routes_called": False, "host_writes": False,
        "runtime_sha_verified": False, "market_readiness": "NO_VERIFICADO",
        "causal_pit_verified": False, "source_contract_ready": False,
        "diagnostic_status": "NO_VERIFICADO", "reason": "PROBE_FAILED",
        "counts": {name: 0 for name in COUNT_KEYS},
        "reject_counts": {name: 0 for name in REJECT_CODES},
        "normalized_field_profile": {
            "raw_provider_aliases": "NOT_PRESERVED_IN_NORMALIZED_CACHE",
            "rows_profiled": 0,
            "value_types": {name: {kind: 0 for kind in VALUE_TYPES}
                            for name in PROFILE_FIELDS},
            "currency_codes": {name: 0 for name in CURRENCY_CLASSES},
            "settlement_codes": {name: 0 for name in SETTLEMENT_CLASSES},
        },
        "byma_matched_by_family": {name: 0 for name in CANONICAL_FAMILIES},
        "catalog_mtime_age_seconds": None,
        "byma_mtime_age_seconds": None,
    }


def _value_type(row: dict, name: str) -> str:
    """Report only a fixed JSON type class; never coerce a provider value."""
    if name not in row:
        return "ABSENT"
    value = row[name]
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, str):
        return "STRING" if value.strip() else "BLANK_STRING"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, list):
        return "ARRAY"
    return "OBJECT"


def _unknown_code_class(code: str) -> str:
    if _NUMERIC_CODE.fullmatch(code):
        return "OTHER_NUMERIC"
    if _ALPHA_CODE.fullmatch(code):
        return "OTHER_ALPHA"
    return "OTHER_TEXT"


def _currency_code_class(row: dict) -> str:
    value = row.get("currency")
    if value is None or isinstance(value, str) and not value.strip():
        return "MISSING"
    if not isinstance(value, str):
        return "NONSTRING"
    code = value.strip().upper()
    if code in CURRENCIES:
        return code
    if code == "EXT":
        # Officially documented as a code; no monetary meaning is inferred.
        return "EXT_UNMAPPED"
    return _unknown_code_class(code)


def _settlement_code_class(row: dict) -> str:
    names = ("settlement", "term", "settlement_code")
    values = [row[name] for name in names if name in row and
              row[name] is not None and row[name] != ""]
    if not values or all(isinstance(value, str) and not value.strip()
                         for value in values):
        return "MISSING"
    if any(not isinstance(value, str) for value in values):
        return "NONSTRING"
    codes = [value.strip().upper() for value in values if value.strip()]
    if not codes:
        return "MISSING"
    translated = {TERMS.get(code, code) for code in codes}
    if len(translated) != 1:
        return "ALIAS_CONFLICT"
    code = codes[0]
    canonical = next(iter(translated))
    if canonical == "A-24HS":
        return "A_24HS_CANONICAL" if code == "A-24HS" else "T1_ALIAS"
    if canonical == "INMEDIATA":
        return "INMEDIATA_CANONICAL" if code == "INMEDIATA" else "T0_ALIAS"
    return _unknown_code_class(code)


def _profile_byma_row(row: object, report: dict) -> None:
    """Fixed-schema type/code histograms from the already normalized cache."""
    if not isinstance(row, dict):
        return
    profile = report["normalized_field_profile"]
    profile["rows_profiled"] += 1
    for name in PROFILE_FIELDS:
        profile["value_types"][name][_value_type(row, name)] += 1
    profile["currency_codes"][_currency_code_class(row)] += 1
    profile["settlement_codes"][_settlement_code_class(row)] += 1


def index_catalog(catalog: dict, report: dict) -> tuple[set, dict]:
    if catalog.get("schema") != CATALOG_SCHEMA:
        raise DiagnosticRejected("CATALOG_SCHEMA_INVALID")
    instruments = catalog.get("instruments")
    if not isinstance(instruments, list):
        raise DiagnosticRejected("CATALOG_SHAPE_INVALID")
    if len(instruments) > MAX_ROWS:
        raise DiagnosticRejected("ROWS_EXCEEDED")
    keys, by_symbol = set(), defaultdict(set)
    counts = report["counts"]
    counts["catalog_rows"] = len(instruments)
    for item in instruments:
        if not isinstance(item, dict):
            counts["catalog_missing_identity"] += 1
            continue
        raw = item.get("canonical_identity")
        if not isinstance(raw, list) or len(raw) != 5 or any(
                not isinstance(part, str) or not part.strip() or
                _text(part) == "UNKNOWN" for part in raw):
            counts["catalog_missing_identity"] += 1
            continue
        key = tuple(_text(part) for part in raw)
        if key in keys:
            counts["catalog_duplicate_identity"] += 1
            continue
        keys.add(key)
        by_symbol[key[0]].add(key)
    counts["catalog_exact_keys"] = len(keys)
    return keys, by_symbol


def summarize_byma(byma: dict, report: dict, keys: set, by_symbol: dict) -> None:
    counts = report["counts"]
    # Producer candidate rc6_source_consolidation.py emits this exact schema
    # and source="BYMA"; A408 selected the same block but never proved schema.
    # An absent/mislabelled/duplicated source must never become a zero-join RCA.
    if byma.get("schema") != BYMA_SCHEMA:
        raise DiagnosticRejected("BYMA_SHAPE_INVALID")
    blocks = byma.get("sources")
    if not isinstance(blocks, list) or len(blocks) > 100:
        raise DiagnosticRejected("BYMA_SHAPE_INVALID")
    source_blocks = [block for block in blocks
                     if isinstance(block, dict) and block.get("source") == "BYMA"]
    if len(source_blocks) != 1:
        raise DiagnosticRejected("BYMA_SHAPE_INVALID")
    extracted = []
    inherited_receipt = any(byma.get(x) not in (None, "")
                            for x in RECEIPT_CLOCKS)
    for block in source_blocks:
        rows = block.get("records")
        if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
            raise DiagnosticRejected("BYMA_SHAPE_INVALID")
        block_receipt = inherited_receipt or any(
            block.get(x) not in (None, "") for x in RECEIPT_CLOCKS)
        extracted.extend((row, block_receipt) for row in rows)
        if len(extracted) > MAX_ROWS:
            raise DiagnosticRejected("ROWS_EXCEEDED")
    counts["byma_rows"] = len(extracted)
    source_unique, source_symbols, matched = set(), defaultdict(set), set()
    for row, block_receipt in extracted:
        _profile_byma_row(row, report)
        visible = row if isinstance(row, dict) else {}
        if isinstance(visible.get("quote"), dict):
            counts["byma_nested_quote_rows"] += 1
        if any(visible.get(x) not in (None, "") for x in NATIVE_CLOCKS):
            counts["byma_native_clock_present"] += 1
        if _aware_clock(visible, NATIVE_CLOCKS):
            counts["byma_native_clock_aware"] += 1
        if block_receipt or any(visible.get(x) not in (None, "")
                                for x in RECEIPT_CLOCKS):
            counts["byma_receipt_clock_present"] += 1
        try:
            key = exact_byma_key(row)
        except DiagnosticRejected as exc:
            report["reject_counts"][str(exc)] += 1
            counts["byma_key_invalid"] += 1
            continue
        counts["byma_key_valid"] += 1
        source_unique.add(key)
        source_symbols[key[0]].add(key)
        if key in keys:
            counts["byma_exact_rows"] += 1
            matched.add(key)
            report["byma_matched_by_family"][key[1]] += 1
            continue
        counts["byma_unmatched_key"] += 1
        same_symbol = by_symbol.get(key[0])
        if not same_symbol:
            counts["byma_symbol_absent"] += 1
            continue
        counts["byma_same_symbol_different_key"] += 1
        for index, label in ((1, "different_family"), (2, "different_market"),
                             (3, "different_currency"), (4, "different_settlement")):
            if not any(candidate[index] == key[index] for candidate in same_symbol):
                counts[label] += 1
    counts["byma_unique_keys"] = len(source_unique)
    counts["byma_exact_unique"] = len(matched)
    counts["byma_multiple_keys_same_symbol"] = sum(
        len(keys) > 1 for keys in source_symbols.values())


def inspect_files(catalog_path=CATALOG_PATH, byma_path=BYMA_PATH,
                  *, now: datetime | None = None):
    report = _new_report()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        report["reason"] = "CLOCK_INVALID"
        return report
    try:
        catalog_raw, catalog_info = safe_bytes(catalog_path, MAX_CATALOG_BYTES)
        report["catalog_mtime_age_seconds"] = _age(
            now.timestamp() - catalog_info.st_mtime)
        catalog = json.loads(catalog_raw.decode("utf-8"))
        del catalog_raw
        if not isinstance(catalog, dict):
            raise DiagnosticRejected("CATALOG_SHAPE_INVALID")
        catalog_keys, by_symbol = index_catalog(catalog, report)
        del catalog
        gc.collect()
        byma_raw, byma_info = safe_bytes(byma_path, MAX_BYMA_BYTES)
        report["byma_mtime_age_seconds"] = _age(now.timestamp() - byma_info.st_mtime)
        byma = json.loads(byma_raw.decode("utf-8"))
        del byma_raw
        if not isinstance(byma, dict):
            raise DiagnosticRejected("BYMA_SHAPE_INVALID")
        summarize_byma(byma, report, catalog_keys, by_symbol)
        report["diagnostic_status"] = "JOIN_COUNTS_ONLY"
        report["reason"] = "EXACT_IDENTITY_DIAGNOSTIC_ONLY"
    except DiagnosticRejected as exc:
        if str(exc) in ERROR_CODES:
            report["reason"] = str(exc)
    except (json.JSONDecodeError, UnicodeError):
        report["reason"] = "JSON_INVALID"
    except (ValueError, OSError, MemoryError, TypeError):
        report["reason"] = "PROBE_FAILED"
    return report


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["host-probe"]:
        print("Usage: A410 bounded host-probe", file=sys.stderr)
        return 64
    try:
        _lock_address_space_limit()
        _require_host_memory_reserve()
    except DiagnosticRejected as exc:
        result = _new_report()
        if str(exc) in ERROR_CODES:
            result["reason"] = str(exc)
    else:
        result = inspect_files()
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
