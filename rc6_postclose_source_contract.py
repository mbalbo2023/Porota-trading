"""Read-only, fail-closed PPI EOD receipt for the current BYMA session.

The observer alone publishes ``source_sync``; a requested session or a fresh
clock cannot replace its exact-key RAW publication attestation.  No network,
trading or SQLite writes occur here. Missing publication remains provisional.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma
from co_market_sessions_hf6 import byma_eod_publication_floor
from cg_paper_workspace import artifact_root, database_path, identity_from_connection
from rc6_audit_evidence.sqlite_snapshot import readonly_copy
from rc6_audit_evidence.sqlite_scratch import SnapshotError


ART = ZoneInfo("America/Argentina/Buenos_Aires")
SOURCE = "PPI_PRODUCTION_HISTORY"
PENDING = "PROVISIONAL_POSTCLOSE_SOURCE_PENDING"
VERIFIED = "VERIFIED_SOURCE_SESSION"
SOURCE_COLUMNS = ("source", "status", "last_attempt_at", "last_success_at", "items", "detail")
SOURCE_CONSUMER = "PPI_HISTORY_INGEST_MONITOR"
HEX_256 = re.compile(r"[a-f0-9]{64}\Z")
RAW_PROVENANCE = "OBSERVER_PPI_HISTORY_CANONICAL_V2_RAW_FULL_KEY"


def _parse_attestation(detail: object, session: str) -> tuple[int, int] | None:
    prefix = f"source_session_verified={session}:"
    matches = [part.strip()[len(prefix):] for part in str(detail or "").split(";")
               if part.strip().startswith(prefix)]
    if len(matches) != 1:
        return None
    matched = re.fullmatch(r"([0-9]{1,3})/([0-9]{1,3})", matches[0])
    if matched is None:
        return None
    verified, required = (int(item) for item in matched.groups())
    return (verified, required) if 0 < verified == required <= 100 else None


def _source_provenance(detail: object, session: str) -> str | None:
    """Validate observer's full-key RAW attestation envelope, not a clock token.

    Actual RAW row verification belongs to the observer's bounded history
    reader; the source_sync detail is its durable, same-session receipt. This
    consumer cannot infer all-family READY or trading authority from it.
    """
    if not isinstance(detail, str) or len(detail) > 16384:
        return None
    parts = [part.strip() for part in detail.split(";")]
    required = (f"session={session}", f"session_requested={session}",
                f"consumer={SOURCE_CONSUMER}", "readiness=NONE",
                f"source_raw_provenance={RAW_PROVENANCE}")
    if any(parts.count(item) != 1 for item in required):
        return None
    if len([part for part in parts if part.startswith("source_raw_provenance=")]) != 1:
        return None
    matches = [part[len("cohort="):] for part in parts if part.startswith("cohort=")]
    if len(matches) != 1 or HEX_256.fullmatch(matches[0]) is None or matches[0] == "0" * 64:
        return None
    if len([part for part in parts if part.startswith("source_session_verified=")]) != 1:
        return None
    # The receipt format is written only after the observer has evaluated a
    # bounded exact-key PPI RAW history batch. No new provider GET is made.
    if not any(re.fullmatch(r"Lote aceptado=[0-9]{1,3}/[0-9]{1,3}", part)
               for part in parts):
        return None
    if not any(re.fullmatch(r"cobertura exacta [0-9]+/[0-9]+", part)
               for part in parts):
        return None
    return matches[0]


def verified_ppi_eod_detail(detail: object, session: str) -> dict | None:
    """Pure shared parser for one exact-session, full-key PPI RAW EOD receipt.

    Neither status nor dates nor DB identity are certified by this parser;
    callers must independently validate those source-row/context properties.
    No SQLite, provider access or host mutation is performed here.
    """
    if not isinstance(session, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", session) is None:
        return None
    attested = _parse_attestation(detail, session)
    cohort = _source_provenance(detail, session)
    if attested is None or cohort is None:
        return None
    return {"verified_exact_keys": attested[0],
            "required_exact_keys": attested[1], "source_cohort_sha256": cohort}


def postclose_eod_receipt(at: datetime, *, db_path: Path | str | None = None) -> dict:
    """Prove current PPI EOD RAW full-key publication or remain provisional."""
    result = {"schema": "rc6.postclose-eod-source.v2", "source": SOURCE,
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
    try:
        database = database_path()
        if (db_path is not None and Path(db_path).absolute() != database):
            raise ValueError("FOREIGN_PAPER_DB")
        if not database.is_file():
            raise ValueError("PAPER_DB_MISSING")
    except (OSError, TypeError, ValueError):
        result["reason"] = "PAPER_SOURCE_DATABASE_UNVERIFIED"
        return result
    try:
        # SQLite mode=ro can create -shm in a live WAL SOURCE. Reuse the
        # existing byte-verified, bounded private image; SQLite reads COPY only.
        # Scratch must already be an owner-private disk root under the PAPER
        # dataset. No global /tmp fallback or implicit directory creation.
        with readonly_copy(database, deadline=time.monotonic() + 10.0,
                           scratch_root=artifact_root(database) / "sqlite-read-scratch",
                           consumer="rc6_postclose_eod") as conn:
            identity = identity_from_connection(conn)
            table = conn.execute("""SELECT type FROM sqlite_master
              WHERE name='source_sync'""").fetchone()
            if not table or table[0] != "table":
                result["reason"] = "PPI_EOD_SOURCE_TABLE_UNVERIFIED"
                return result
            columns = tuple(conn.execute("PRAGMA table_info(source_sync)"))
            if (tuple(item[1] for item in columns) != SOURCE_COLUMNS
                    or tuple(str(item[2]).upper() for item in columns) !=
                       ("TEXT", "TEXT", "TEXT", "TEXT", "INTEGER", "TEXT")
                    or columns[0][5] != 1 or any(item[5] for item in columns[1:])
                    or any(columns[i][3] != 1 for i in (1, 2, 4, 5))):
                result["reason"] = "PPI_EOD_SOURCE_SCHEMA_UNVERIFIED"
                return result
            row = conn.execute("""SELECT status,last_attempt_at,last_success_at,detail
              FROM source_sync
              WHERE source=?""", (SOURCE,)).fetchone()
        if not row or row[0] != "VERDE" or not row[2]:
            result["reason"] = "PPI_EOD_SOURCE_NOT_GREEN"
            return result
        successful = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
        attempted = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
        if successful.tzinfo is None or successful.utcoffset() is None:
            result["reason"] = "PPI_EOD_SOURCE_CLOCK_UNVERIFIED"
            return result
        if (attempted.tzinfo is None or attempted.utcoffset() is None
                or attempted < successful or attempted > at
                or successful.astimezone(ART).date() != now.date()
                or successful < byma_eod_publication_floor(now)
                or successful > at):
            result["reason"] = "PPI_EOD_SOURCE_STALE_OR_FUTURE"
            return result
        attested = _parse_attestation(row[3], result["session"])
        if attested is None:
            result["reason"] = "PPI_EOD_EXACT_SESSION_NOT_ATTESTED"
            return result
        parsed = verified_ppi_eod_detail(row[3], result["session"])
        if parsed is None:
            result["reason"] = "PPI_EOD_SOURCE_PROVENANCE_UNVERIFIED"
            return result
        # Do not promote until the snapshot context has also verified that the
        # source stayed byte-for-byte unchanged throughout the entire read.
        validated = {
            "state": VERIFIED, "reason": "PPI_EOD_EXACT_SOURCE_SESSION_VERIFIED",
            "verified_exact_keys": parsed["verified_exact_keys"],
            "required_exact_keys": parsed["required_exact_keys"],
            "source_success_at": successful.astimezone(ART).isoformat(),
            "paper_dataset_id": identity["dataset_id"],
            "source_detail_sha256": sha256(row[3].encode("utf-8")).hexdigest(),
            "source_cohort_sha256": parsed["source_cohort_sha256"],
            "source_raw_provenance": RAW_PROVENANCE,
        }
        result.update(validated)
    except (OSError, sqlite3.Error, ValueError, TypeError, SnapshotError):
        result["state"] = PENDING
        result["reason"] = "PPI_EOD_RECEIPT_READ_UNVERIFIED"
    return result


def verified_current_source(receipt: object, at: datetime) -> bool:
    """Reject stale or forged source tokens even if their state says verified."""
    if not isinstance(receipt, dict) or at.tzinfo is None or at.utcoffset() is None:
        return False
    try:
        if not byma.es_dia_habil_operativo(at.astimezone(ART).date()):
            return False
    except (KeyError, TypeError, ValueError, OSError):
        return False
    if (receipt.get("schema") != "rc6.postclose-eod-source.v2" or
            receipt.get("source") != SOURCE or
            receipt.get("source_raw_provenance") != RAW_PROVENANCE or
            receipt.get("scope") != "BOUNDED_PPI_FULL_KEY_HISTORY_BATCH_ONLY" or
            receipt.get("all_families_approved") is not False or
            receipt.get("paper_entry_authority") is not False or
            receipt.get("state") != VERIFIED or
            receipt.get("session") != at.astimezone(ART).date().isoformat()):
        return False
    try:
        dataset_id = str(receipt.get("paper_dataset_id"))
        if str(uuid.UUID(dataset_id)) != dataset_id:
            return False
        if (any(HEX_256.fullmatch(str(receipt.get(key))) is None
                or str(receipt.get(key)) == "0" * 64
                for key in ("source_detail_sha256", "source_cohort_sha256"))):
            return False
        verified = int(receipt.get("verified_exact_keys"))
        required = int(receipt.get("required_exact_keys"))
        success = datetime.fromisoformat(str(receipt.get("source_success_at")).replace("Z", "+00:00"))
        return (0 < verified == required <= 100 and
                success.tzinfo is not None and success.utcoffset() is not None and
                success.astimezone(ART).date() == at.astimezone(ART).date() and
                byma_eod_publication_floor(at) <= success <= at)
    except (ValueError, TypeError):
        return False
