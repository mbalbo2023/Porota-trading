"""Read-only, fail-closed receipt for a postclose PPI EOD source session.

The independent observer is the only writer of source_sync. As of A316 it
emits source_session_verified=DATE:N/N solely after checking canonical PPI RAW
full-key rows, date and first-known timestamp. The requested `session=DATE`
alone is *never* a source receipt or final-report authority.

This checker performs no network calls, trading or SQLite writes. An external
provider missing its same-session publication remains AMBER/provisional.
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma
from co_market_sessions_hf6 import byma_eod_publication_floor


ART = ZoneInfo("America/Argentina/Buenos_Aires")
DEFAULT_PAPER_DB = Path(os.getenv(
    "PAPER_V17_DB_PATH", os.getenv("POROTA_PAPER_DB", "/app/data/paper_v17/observer_v17.db")))
SOURCE = "PPI_PRODUCTION_HISTORY"
PENDING = "PROVISIONAL_POSTCLOSE_SOURCE_PENDING"
VERIFIED = "VERIFIED_SOURCE_SESSION"


def _parse_attestation(detail: object, session: str) -> tuple[int, int] | None:
    prefix = f"source_session_verified={session}:"
    values = [str(part).strip()[len(prefix):]
              for part in str(detail or "").split(";")
              if str(part).strip().startswith(prefix)]
    if len(values) != 1:
        return None
    try:
        verified, required = (int(item) for item in values[0].split("/"))
    except (TypeError, ValueError):
        return None
    return (verified, required) if verified > 0 and verified == required else None


def postclose_eod_receipt(at: datetime, *, db_path: Path | str | None = None) -> dict:
    """Minimal source proof; time floor never substitutes for PPI publication."""
    result = {"schema": "rc6.postclose-eod-source.v1", "source": SOURCE,
              "state": PENDING, "session": None,
              "reason": "NO_VERIFIED_SOURCE_RECEIPT", "verified_exact_keys": 0,
              "required_exact_keys": 0, "source_success_at": None,
              "scope": "BOUNDED_PPI_FULL_KEY_HISTORY_BATCH_ONLY",
              "all_families_approved": False, "paper_entry_authority": False}
    if at.tzinfo is None or at.utcoffset() is None:
        result["reason"] = "CLOCK_MISSING_TIMEZONE"
        return result
    now = at.astimezone(ART)
    result["session"] = now.date().isoformat()
    try:
        if not byma.es_dia_habil_operativo(now.date()):
            result["reason"] = "NON_TRADING_DAY"
            return result
    except (KeyError, TypeError, ValueError, OSError):
        result["reason"] = "MARKET_CALENDAR_UNVERIFIED"
        return result
    if now < byma_eod_publication_floor(now):
        result["reason"] = "BYMA_EOD_PUBLICATION_FLOOR_NOT_REACHED"
        return result
    database = Path(db_path) if db_path is not None else DEFAULT_PAPER_DB
    if not database.is_absolute() or database.is_symlink() or not database.is_file():
        result["reason"] = "PAPER_SOURCE_DATABASE_UNVERIFIED"
        return result
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.3) as conn:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute("""SELECT status,last_success_at,detail FROM source_sync
              WHERE source=?""", (SOURCE,)).fetchone()
        if not row or row[0] != "VERDE" or not row[1]:
            result["reason"] = "PPI_EOD_SOURCE_NOT_GREEN"
            return result
        successful = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
        if successful.tzinfo is None or successful.utcoffset() is None:
            result["reason"] = "PPI_EOD_SOURCE_CLOCK_UNVERIFIED"
            return result
        if (successful.astimezone(ART).date() != now.date()
                or successful < byma_eod_publication_floor(now)
                or successful > at):
            result["reason"] = "PPI_EOD_SOURCE_STALE_OR_FUTURE"
            return result
        attested = _parse_attestation(row[2], result["session"])
        if attested is None:
            result["reason"] = "PPI_EOD_EXACT_SESSION_NOT_ATTESTED"
            return result
        result.update(state=VERIFIED, reason="PPI_EOD_EXACT_SOURCE_SESSION_VERIFIED",
                      verified_exact_keys=attested[0], required_exact_keys=attested[1],
                      source_success_at=successful.astimezone(ART).isoformat())
    except (OSError, sqlite3.Error, ValueError, TypeError):
        result["reason"] = "PPI_EOD_RECEIPT_READ_UNVERIFIED"
    return result


def verified_current_source(receipt: object, at: datetime) -> bool:
    """Revalidate a passed receipt; a stale/forged status token is not enough."""
    if not isinstance(receipt, dict) or at.tzinfo is None or at.utcoffset() is None:
        return False
    try:
        if not byma.es_dia_habil_operativo(at.astimezone(ART).date()):
            return False
    except (KeyError, TypeError, ValueError, OSError):
        return False
    if receipt.get("state") != VERIFIED or receipt.get("session") != at.astimezone(ART).date().isoformat():
        return False
    try:
        n = int(receipt.get("verified_exact_keys"))
        d = int(receipt.get("required_exact_keys"))
        seen = datetime.fromisoformat(str(receipt.get("source_success_at")).replace("Z", "+00:00"))
        return (n > 0 and n == d and seen.tzinfo is not None
                and seen.utcoffset() is not None
                and byma_eod_publication_floor(at) <= seen <= at)
    except (ValueError, TypeError):
        return False
