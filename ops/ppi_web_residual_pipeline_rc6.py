#!/usr/bin/env python3
"""RC6 core pipeline for PPI Web residual ingestion.

Fail-closed design:
- No network or browser implementation lives here.
- The API historical writer must be completed before web execution is enabled.
- Only read-only records are accepted.
- Raw evidence and normalized records are separated.
- Reconciliation never overwrites existing API canonical data.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

TERMINAL_API_STATUS = {"COMPLETED", "CLOSED", "DONE"}
READONLY_CONFIRM = "PPI_WEB_READ_ONLY_CONFIRMED"

FAMILY_CONTRACT_FIELDS: dict[str, tuple[str, ...]] = {
    "ACCIONES": ("symbol", "market", "currency"),
    "CEDEARS": ("symbol", "market", "currency"),
    "BONOS": ("symbol", "market", "currency", "maturity_date"),
    "LETRAS": ("symbol", "market", "currency", "maturity_date"),
    "ON": ("symbol", "market", "currency", "maturity_date"),
    "OPCIONES": ("symbol", "market", "underlying", "option_type", "strike", "expiry_date"),
    "FUTUROS": ("symbol", "market", "underlying", "expiry_date", "contract_size"),
    "FCI": ("symbol", "currency"),
    "ETF": ("symbol", "market", "currency"),
    "ACCIONES USA": ("symbol", "market", "currency"),
    "FCI EXTERIOR": ("symbol", "currency"),
    "CAUCIONES": ("symbol", "market", "currency", "term_days"),
    "LICITACIONES": ("symbol", "currency"),
    "INDICES": ("symbol", "market"),
}


@dataclass(frozen=True)
class WebResidualRecord:
    symbol: str
    instrument_type: str
    market: str
    settlement: str
    residual_class: str
    source_url: str
    fields: dict[str, object]

    @property
    def identity_key(self) -> tuple[str, str, str, str]:
        return (
            self.symbol.strip().upper(),
            self.instrument_type.strip().upper(),
            self.market.strip().upper(),
            self.settlement.strip().upper(),
        )


def execution_gate(api_status: str, readonly_confirmation: str, writer_active: bool) -> None:
    if writer_active:
        raise RuntimeError("PPI API historical writer is still active")
    if api_status.strip().upper() not in TERMINAL_API_STATUS:
        raise RuntimeError(f"API ingestion not terminal: {api_status!r}")
    if readonly_confirmation.strip().upper() != READONLY_CONFIRM:
        raise RuntimeError("explicit PPI Web read-only confirmation is missing")


def normalize_family(value: str) -> str:
    return " ".join(str(value).strip().upper().split())


def normalize_record(raw: Mapping[str, object]) -> WebResidualRecord:
    required = ("symbol", "instrument_type", "market", "settlement", "residual_class", "source_url", "fields")
    missing = [k for k in required if raw.get(k) in (None, "")]
    if missing:
        raise ValueError("missing web residual fields: " + ",".join(missing))
    if not isinstance(raw["fields"], Mapping):
        raise ValueError("fields must be an object")
    source_url = str(raw["source_url"]).strip()
    if not source_url.startswith("https://"):
        raise ValueError("source_url must be https")
    return WebResidualRecord(
        symbol=str(raw["symbol"]).strip(),
        instrument_type=normalize_family(str(raw["instrument_type"])),
        market=str(raw["market"]).strip().upper(),
        settlement=str(raw["settlement"]).strip().upper(),
        residual_class=str(raw["residual_class"]).strip().upper(),
        source_url=source_url,
        fields={str(k): v for k, v in dict(raw["fields"]).items()},
    )


def validate_contract(record: WebResidualRecord) -> list[str]:
    required = FAMILY_CONTRACT_FIELDS.get(record.instrument_type, ("symbol",))
    missing: list[str] = []
    merged = {"symbol": record.symbol, "market": record.market, **record.fields}
    for field in required:
        value = merged.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


def paginate(items: Sequence[Mapping[str, object]], page_size: int) -> Iterator[list[Mapping[str, object]]]:
    if page_size <= 0:
        raise ValueError("page_size must be > 0")
    for idx in range(0, len(items), page_size):
        yield list(items[idx: idx + page_size])


def reconcile(
    records: Iterable[WebResidualRecord],
    api_covered_keys: set[tuple[str, str, str, str]],
) -> tuple[list[WebResidualRecord], list[WebResidualRecord]]:
    accepted: list[WebResidualRecord] = []
    skipped_existing: list[WebResidualRecord] = []
    seen: set[tuple[str, str, str, str]] = set()
    for record in records:
        key = record.identity_key
        if key in seen:
            raise ValueError(f"duplicate web identity: {key}")
        seen.add(key)
        if key in api_covered_keys:
            skipped_existing.append(record)
        else:
            accepted.append(record)
    return accepted, skipped_existing


def process_capture(raw_rows: Sequence[Mapping[str, object]], api_covered_keys: set[tuple[str, str, str, str]]) -> dict[str, object]:
    normalized = [normalize_record(row) for row in raw_rows]
    accepted, skipped = reconcile(normalized, api_covered_keys)
    incomplete_contracts = []
    for record in accepted:
        missing = validate_contract(record)
        if missing:
            incomplete_contracts.append({"identity": record.identity_key, "missing": missing})
    return {
        "mode": "PPI_WEB_READ_ONLY",
        "input_rows": len(raw_rows),
        "accepted_rows": len(accepted),
        "skipped_existing_api": len(skipped),
        "incomplete_contracts": incomplete_contracts,
        "records": [asdict(r) for r in accepted],
    }


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True, help="Read-only captured PPI Web JSON array")
    parser.add_argument("--api-covered", type=Path, help="JSON array of [symbol,type,market,settlement] keys")
    parser.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args()

    execution_gate(
        api_status=os.environ.get("PPI_API_INGEST_STATUS", ""),
        readonly_confirmation=os.environ.get("PPI_WEB_MODE", ""),
        writer_active=os.environ.get("PPI_API_WRITER_ACTIVE", "1") == "1",
    )

    raw = load_json(args.capture)
    if not isinstance(raw, list):
        raise ValueError("capture must be a JSON array")
    covered: set[tuple[str, str, str, str]] = set()
    if args.api_covered:
        payload = load_json(args.api_covered)
        if not isinstance(payload, list):
            raise ValueError("api-covered must be a JSON array")
        covered = {tuple(str(v).strip().upper() for v in row) for row in payload if isinstance(row, list) and len(row) == 4}
    result = process_capture(raw, covered)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
