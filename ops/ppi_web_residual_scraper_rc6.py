#!/usr/bin/env python3
"""RC6 scaffold for PPI Web residual processing.

This module intentionally does not perform authenticated browser actions yet.
It validates the residual manifest and enforces read-only execution semantics so
that the Web extraction layer can be attached after the PPI API pass closes.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ALLOWED_RESIDUAL_CLASSES = {
    "NO_PROVIDER_ROWS",
    "PROVIDER_INVALID",
    "PARTIAL_VALID",
    "HARD_PROVIDER_ERROR",
}

REQUIRED_FIELDS = (
    "symbol",
    "instrument_type",
    "market",
    "settlement",
    "residual_class",
)


@dataclass(frozen=True)
class ResidualIdentity:
    symbol: str
    instrument_type: str
    market: str
    settlement: str
    residual_class: str

    @classmethod
    def from_dict(cls, row: dict) -> "ResidualIdentity":
        missing = [key for key in REQUIRED_FIELDS if not row.get(key)]
        if missing:
            raise ValueError(f"missing required fields: {','.join(missing)}")
        residual_class = str(row["residual_class"]).upper()
        if residual_class not in ALLOWED_RESIDUAL_CLASSES:
            raise ValueError(f"invalid residual_class={residual_class}")
        return cls(
            symbol=str(row["symbol"]),
            instrument_type=str(row["instrument_type"]),
            market=str(row["market"]),
            settlement=str(row["settlement"]),
            residual_class=residual_class,
        )


def load_jsonl(path: Path) -> list[ResidualIdentity]:
    identities: list[ResidualIdentity] = []
    seen: set[tuple[str, str, str, str]] = set()
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            identity = ResidualIdentity.from_dict(row)
            key = (
                identity.symbol,
                identity.instrument_type,
                identity.market,
                identity.settlement,
            )
            if key in seen:
                raise ValueError(f"duplicate residual identity at line {lineno}: {key}")
            seen.add(key)
            identities.append(identity)
    return identities


def summarize(rows: Iterable[ResidualIdentity]) -> dict[str, object]:
    rows = list(rows)
    by_class: dict[str, int] = {}
    by_family: dict[str, int] = {}
    for row in rows:
        by_class[row.residual_class] = by_class.get(row.residual_class, 0) + 1
        by_family[row.instrument_type] = by_family.get(row.instrument_type, 0) + 1
    return {
        "mode": "READ_ONLY_SCAFFOLD",
        "mass_scraping_started": False,
        "identities": len(rows),
        "by_residual_class": dict(sorted(by_class.items())),
        "by_family": dict(sorted(by_family.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("validate",),
        default="validate",
        help="Only validation is enabled until API ingestion closeout.",
    )
    args = parser.parse_args()
    rows = load_jsonl(args.manifest)
    print(json.dumps(summarize(rows), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
