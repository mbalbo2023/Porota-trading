#!/usr/bin/env python3
"""Migrate an RC6 IOL cache to the fail-safe continuation contract.

This is a local, policy-only migration.  It never calls IOL, invents a market
value, zero-fills a missing section, or authorizes an order route.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


FALLBACK_ORDER = [
    "IOL_LIVE_BOUNDED_RETRY",
    "IOL_LAST_KNOWN_GOOD",
    "PPI_PRIMARY",
    "BYMA_PUBLIC_COMPLEMENTARY",
]
CONTINUATION_STATE = "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL"


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def has_usable_reference(payload: dict[str, Any]) -> bool:
    if _nonempty_list(payload.get("records")) or _nonempty_list(payload.get("fci")):
        return True
    cauciones = payload.get("cauciones")
    return isinstance(cauciones, dict) and any(
        _nonempty_list(rows) for rows in cauciones.values()
    )


def migrate(payload: dict[str, Any], *, migrated_at: str | None = None) -> dict[str, Any]:
    if payload.get("real_money_authorized") is True:
        raise ValueError("IOL_REFERENCE_REAL_MONEY_AUTHORIZED")
    result = dict(payload)
    result["real_money_authorized"] = False
    result["fallback_order"] = list(FALLBACK_ORDER)
    result["continuation_state"] = CONTINUATION_STATE

    usable = has_usable_reference(result)
    live_sections = result.get("live_sections")
    live = isinstance(live_sections, list) and bool(live_sections)
    last_good = result.get("last_known_good_at")
    if live and not last_good:
        # A live section was explicitly recorded by the collector at refreshed_at.
        # This is provenance already present in the payload, not a fabricated tick.
        last_good = result.get("refreshed_at")
        result["last_known_good_at"] = last_good
    if not usable or not last_good:
        result["cache_state"] = "SOURCE_UNAVAILABLE"

    states = result.get("section_states")
    if not isinstance(states, dict) or not states:
        result["section_states"] = {"global": "SOURCE_UNAVAILABLE_NO_LKG"}

    prior_migration = result.get("contract_migration")
    if not isinstance(prior_migration, dict):
        prior_migration = {}
    result["contract_migration"] = {
        "schema": "rc6-iol-continuation-contract-v1",
        "migrated_at": prior_migration.get("migrated_at") or migrated_at
        or datetime.now(timezone.utc).isoformat(),
        "policy_only": True,
        "market_values_preserved": True,
        "zero_fill": False,
        "real_routes": [],
    }
    return result


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    stat = path.stat()
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent,
        prefix=".iol-reference-contract-", suffix=".tmp", delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, stat.st_mode & 0o777)
    if hasattr(os, "chown"):
        os.chown(temporary, stat.st_uid, stat.st_gid)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("IOL_REFERENCE_NOT_AN_OBJECT")
    migrated = migrate(payload)
    changed = migrated != payload
    if changed:
        _atomic_write(args.path, migrated)
    print(
        "IOL_REFERENCE_CONTRACT_MIGRATION=GREEN"
        f"|CHANGED={str(changed).lower()}"
        f"|CACHE_STATE={migrated.get('cache_state')}"
        f"|LKG={migrated.get('last_known_good_at') or 'NONE'}"
        "|ZERO_FILL=false|REAL_ROUTES=[]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
