"""RC4 read-only provider for policy-gate observational inputs.

Builds expectancy, breadth/regime and sector observations from the same ledger
sources used by operational introspection.  It never changes configuration,
never grants READY_PAPER and never routes an order.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ch_empirical_learning import empirical_expectancy
from ci_operational_context import breadth_observation, sector_observation

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _tables(connection):
    return {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}


def _local_day_start(at=None) -> str:
    if at is None:
        value = datetime.now(TZ)
    elif isinstance(at, datetime):
        value = at
    else:
        value = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(TZ)
    return datetime.combine(value.date(), datetime.min.time(), TZ).isoformat()


def _closed_expectancy(connection) -> list[dict]:
    rows = [dict(r) for r in connection.execute(
        """WITH ranked AS (
          SELECT status,currency,net_pnl,
            ROW_NUMBER() OVER (PARTITION BY currency
              ORDER BY julianday(closed_at) DESC,paper_id DESC) sample_rank
          FROM paper_positions WHERE status='CLOSED'
        ) SELECT status,currency,net_pnl FROM ranked WHERE sample_rank<=100
          ORDER BY currency,sample_rank"""
    )]
    return empirical_expectancy(rows, minimum_sample=30)


def _breadth(connection, day_start: str) -> dict:
    rows = [dict(r) for r in connection.execute(
        """WITH ranked AS (
          SELECT symbol,currency,last,
            ROW_NUMBER() OVER (PARTITION BY symbol,currency
              ORDER BY julianday(COALESCE(trade_at,observed_at)),id) first_rank,
            ROW_NUMBER() OVER (PARTITION BY symbol,currency
              ORDER BY julianday(COALESCE(trade_at,observed_at)) DESC,id DESC) last_rank
          FROM market_snapshots WHERE last_kind='TRADE' AND CAST(last AS REAL)>0
            AND julianday(COALESCE(trade_at,observed_at))>=julianday(?)
        ) SELECT symbol,currency,
          MAX(CASE WHEN first_rank=1 THEN last END) first_price,
          MAX(CASE WHEN last_rank=1 THEN last END) last_price
          FROM ranked GROUP BY symbol,currency ORDER BY symbol,currency""",
        (day_start,),
    )]
    return breadth_observation(rows, minimum_symbols=4)


def _explicit_sector_map(connection=None) -> dict[tuple, dict]:
    """Load only reviewed, sourced sector mappings; never infer from ticker/catalog text."""
    del connection
    path = Path(os.getenv("POROTA_SECTOR_MAP_PATH",
                          str(Path(__file__).with_name("POROTA_SECTOR_MAP_V1.csv"))))
    if not path.is_file():
        return {}
    result = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            reviewed = str(row.get("reviewed") or "").strip().lower() in {"1","true","yes","si","sí"}
            source = str(row.get("source") or "").strip()
            sector = str(row.get("sector") or "").strip()
            if not (reviewed and source and sector):
                continue
            key = (
                str(row.get("ticker") or "").upper().strip(),
                str(row.get("family") or "").upper().strip(),
                str(row.get("market") or "").upper().strip(),
                str(row.get("currency") or "").upper().strip(),
                str(row.get("settlement") or "").upper().strip(),
            )
            if not all(key):
                continue
            result[key] = {
                "sector": sector,
                "source": source,
                "author": str(row.get("author") or "").strip(),
                "effective_at": str(row.get("effective_at") or "").strip(),
            }
    return result


def collect(store, *, at=None, candidate=None) -> dict:
    """Return read-only policy observations and explicit candidate sector.

    Missing tables/data are represented explicitly so SHADOW can keep observing
    while BINDING can fail closed through ``ck_policy_gate_hf6.evaluate``.
    """
    day_start = _local_day_start(at)
    with store.connect() as c:
        tables = _tables(c)
        expectancy = _closed_expectancy(c) if "paper_positions" in tables else []
        breadth = (_breadth(c, day_start) if "market_snapshots" in tables else
                   {"state":"INSUFFICIENT_SAMPLE","symbols":0,"policy":"ALERT_ONLY","binding":False})
        sector_map = _explicit_sector_map()
        positions = []
        if "paper_positions" in tables:
            for row in c.execute(
                """SELECT paper_id,symbol,asset_class,market,currency,settlement
                   FROM paper_positions WHERE status='OPEN' ORDER BY opened_at"""
            ):
                item = dict(row)
                key = (
                    str(item.get("symbol") or "").upper(),
                    str(item.get("asset_class") or "").upper(),
                    str(item.get("market") or "").upper(),
                    str(item.get("currency") or "").upper(),
                    str(item.get("settlement") or "").upper(),
                )
                mapped = sector_map.get(key)
                item["sector"] = mapped.get("sector") if mapped else None
                item["sector_source"] = mapped.get("source") if mapped else None
                positions.append(item)
        sectors = sector_observation(positions)

    candidate_sector = None
    if isinstance(candidate, dict):
        key = (
            str(candidate.get("symbol") or "").upper(),
            str(candidate.get("family") or candidate.get("asset_class") or "").upper(),
            str(candidate.get("market") or "").upper(),
            str(candidate.get("currency") or "").upper(),
            str(candidate.get("settlement") or "").upper(),
        )
        mapped = sector_map.get(key)
        candidate_sector = mapped.get("sector") if mapped else None
    return {
        "expectancy_samples": expectancy,
        "breadth": breadth,
        "sectors": sectors,
        "candidate_sector": candidate_sector,
        "sector_mapping_rows": len(sector_map),
        "sector_mapping_source": "REVIEWED_VERSIONED_FILE_ONLY",
        "source": "RC4_POLICY_CONTEXT_READONLY",
    }
