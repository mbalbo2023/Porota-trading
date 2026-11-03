#!/usr/bin/env python3
"""A410: privacy-safe, bounded BYMA/export identity JOIN diagnostic.

No provider calls, database connections, filesystem writes or order routes.
Only two previously published JSON files are read.  Raw symbols, quotes,
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
import stat
import sys


SCHEMA = "rc6.a410.byma-catalog-join-readonly.v1"
CATALOG_SCHEMA = "rc6-full-universe-instrument-evidence-v1"
CATALOG_PATH = Path("/opt/porota-trading/data/market/rc6_instrument_evidence_latest.json")
BYMA_PATH = Path("/opt/porota-trading/data/market/rc6_public_sources_latest.json")
MAX_CATALOG_BYTES = 48 * 1024 * 1024
MAX_BYMA_BYTES = 12 * 1024 * 1024
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
ERROR_CODES = frozenset({
    "FILE_MISSING", "PATH_UNSAFE", "FILE_UNSAFE", "READ_UNAVAILABLE",
    "FILE_SIZE_INVALID", "FILE_CHANGED", "JSON_INVALID",
    "CATALOG_SCHEMA_INVALID", "CATALOG_SHAPE_INVALID",
    "BYMA_SHAPE_INVALID", "ROWS_EXCEEDED", "CLOCK_INVALID",
    "PROBE_FAILED",
})


class DiagnosticRejected(Exception):
    """A fixed public code, never an exception with raw content."""


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
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
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
    present = [_text(row[key]) for key in keys if row.get(key) not in (None, "")]
    if not present:
        raise DiagnosticRejected("MISSING_" + label)
    translated = [(aliases or {}).get(value, value) for value in present]
    if len(set(translated)) != 1:
        raise DiagnosticRejected(label + "_CONFLICT")
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
        "byma_matched_by_family": {name: 0 for name in CANONICAL_FAMILIES},
        "catalog_mtime_age_seconds": None,
        "byma_mtime_age_seconds": None,
    }


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
                not isinstance(part, str) or not part.strip() for part in raw):
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
    blocks = byma.get("sources")
    if not isinstance(blocks, list) or len(blocks) > 100:
        raise DiagnosticRejected("BYMA_SHAPE_INVALID")
    extracted = []
    inherited_receipt = any(byma.get(x) not in (None, "")
                            for x in RECEIPT_CLOCKS)
    for block in blocks:
        if not isinstance(block, dict) or _text(block.get("source")) != "BYMA":
            continue
        rows = block.get("records")
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise DiagnosticRejected("BYMA_SHAPE_INVALID")
        block_receipt = inherited_receipt or any(
            block.get(x) not in (None, "") for x in RECEIPT_CLOCKS)
        extracted.extend((row, block_receipt) for row in rows)
        if len(extracted) > MAX_ROWS:
            raise DiagnosticRejected("ROWS_EXCEEDED")
    counts["byma_rows"] = len(extracted)
    source_unique, source_symbols, matched = set(), defaultdict(set), set()
    for row, block_receipt in extracted:
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
    result = inspect_files()
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
