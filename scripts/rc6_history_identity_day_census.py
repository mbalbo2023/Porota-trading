"""One exact historical identity/day census on the installed PAPER container.

Read-only diagnostic. No provider request, schema migration, data promotion,
trading admission, full-table scan, or assertion about legacy/raw coverage.
The GitHub Action streams this reviewed file over SSH to ``python -``; it does
not install a file on the host.
"""
from __future__ import annotations

import base64
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import quote


OBSERVER_DB = Path("/app/data/paper_v17/observer_v17.db")
HISTORY_DB = Path("/app/data/market_history.db")
FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
})
FIRST_DAY = date(2026, 10, 1)
LAST_DAY = date(2026, 10, 30)
VERSION_LIMIT = 64
CANONICAL_LIMIT = 16
SECONDS_BUDGET = 3
EXACT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,39}\Z")


class CensusError(ValueError):
    pass


def target_from_base64(raw: str) -> tuple[str, str, str, str, str, str]:
    if not isinstance(raw, str) or len(raw) > 1024 or not re.fullmatch(r"[A-Za-z0-9+/=]+", raw):
        raise CensusError("CENSUS_TARGET_ENCODING_INVALID")
    try:
        payload = base64.b64decode(raw, validate=True)
        target = json.loads(payload)
    except (ValueError, UnicodeDecodeError, TypeError):
        raise CensusError("CENSUS_TARGET_ENCODING_INVALID") from None
    if (not isinstance(target, list) or len(target) != 6 or
            any(not isinstance(item, str) or not EXACT.fullmatch(item) for item in target)):
        raise CensusError("CENSUS_EXACT_IDENTITY_REQUIRED")
    symbol, family, market, currency, settlement, day = target
    if family not in FAMILIES or any(x in {"UNKNOWN", "NONE"} for x in target):
        raise CensusError("CENSUS_EXACT_IDENTITY_REQUIRED")
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        raise CensusError("CENSUS_DAY_INVALID") from None
    if parsed.isoformat() != day or not FIRST_DAY <= parsed <= LAST_DAY:
        raise CensusError("CENSUS_DAY_OUT_OF_BOUNDS")
    return symbol, family, market, currency, settlement, day


def _connection(path: Path, deadline: float):
    path = Path(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise CensusError("CENSUS_DB_PATH_INVALID")
        uri = "file:" + quote(str(path.absolute()), safe="/") + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=.2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=200")
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        return connection
    except (OSError, sqlite3.Error):
        raise CensusError("CENSUS_DB_READ_UNAVAILABLE") from None


def _indexed(connection, table: str, index: str, prefix: tuple[str, ...]):
    names = {row[1] for row in connection.execute(f"PRAGMA index_list({table})")}
    if index not in names:
        raise CensusError("CENSUS_EXACT_INDEX_MISSING")
    columns = tuple(row[2] for row in connection.execute(f"PRAGMA index_info({index})"))
    if columns[:len(prefix)] != prefix:
        raise CensusError("CENSUS_EXACT_INDEX_MISMATCH")


def _digest(rows):
    body = json.dumps([tuple(row) for row in rows], sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return sha256(body.encode()).hexdigest()


def census(observer_db: Path, history_db: Path, target, *, seconds=SECONDS_BUDGET):
    symbol, family, market, currency, settlement, day = target
    # The target is independently parsed on the runner and on the host.
    target_from_base64(base64.b64encode(json.dumps(list(target)).encode()).decode())
    deadline = time.monotonic() + seconds
    observer = _connection(observer_db, deadline)
    try:
        state = observer.execute(
            "SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if (state is None or state["mode"] != "PRODUCTION_PAPER" or
                type(state["real_orders_sent"]) is not int or state["real_orders_sent"] != 0):
            raise CensusError("CENSUS_PAPER_ZERO_UNVERIFIED")
    finally:
        observer.close()

    history = _connection(history_db, deadline)
    try:
        _indexed(history, "history_versions_v2", "idx_history_versions_v2_identity",
                 ("symbol", "instrument_type", "market", "currency", "settlement", "date"))
        # Product's canonical PRIMARY KEY has the whole exact identity and day
        # before either basis. The named lookup index places day after basis,
        # so it could traverse an instrument's entire history for this probe.
        _indexed(history, "history_canonical_v2", "sqlite_autoindex_history_canonical_v2_1",
                 ("symbol", "instrument_type", "market", "currency", "settlement", "date"))
        identity = (symbol, family, market, currency, settlement, day)
        versions = history.execute("""SELECT id,source,version_known_at,payload_hash,
                price_basis,adjustment_basis FROM history_versions_v2
                INDEXED BY idx_history_versions_v2_identity
                WHERE symbol=? AND instrument_type=? AND market=? AND currency=?
                  AND settlement=? AND date=?
                ORDER BY price_basis,adjustment_basis,id DESC LIMIT ?""",
                (*identity, VERSION_LIMIT + 1)).fetchall()
        canonical = history.execute("""SELECT version_id,source,version_known_at,
                price_basis,adjustment_basis,close FROM history_canonical_v2
                INDEXED BY sqlite_autoindex_history_canonical_v2_1
                WHERE symbol=? AND instrument_type=? AND market=? AND currency=?
                  AND settlement=? AND date=?
                ORDER BY price_basis,adjustment_basis LIMIT ?""",
                (*identity, CANONICAL_LIMIT + 1)).fetchall()
        if time.monotonic() >= deadline:
            raise CensusError("CENSUS_TIME_BUDGET_EXCEEDED")
        if len(versions) > VERSION_LIMIT or len(canonical) > CANONICAL_LIMIT:
            raise CensusError("CENSUS_ROW_BUDGET_EXCEEDED")
        # Check observed PAPER state again; never infer safety from a single
        # before-read if the runtime changed while the history DB was queried.
        post = _connection(observer_db, deadline)
        try:
            after = post.execute(
                "SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
            if (after is None or after["mode"] != "PRODUCTION_PAPER" or
                    type(after["real_orders_sent"]) is not int or after["real_orders_sent"] != 0):
                raise CensusError("CENSUS_PAPER_ZERO_UNVERIFIED")
        finally:
            post.close()
        return {
            "schema": "rc6.history.identity_day.readonly.v1",
            "state": "EXACT_V2_READ_ONLY" if versions or canonical else "EMPTY_EXACT_V2_ONLY",
            "identity": list(identity), "versions": len(versions),
            "canonical": len(canonical), "versions_sha256": _digest(versions),
            "canonical_sha256": _digest(canonical), "paper_mode": "PRODUCTION_PAPER",
            "real_orders_sent": 0, "provider_raw": "NO_VERIFICADO",
            "legacy_history": "NO_VERIFICADO", "ready_implication": "NONE",
            "history_v2_writes": 0, "host_sql": "SELECT_ONLY_INDEXED_BOUNDED",
        }
    except sqlite3.OperationalError:
        raise CensusError("CENSUS_QUERY_BUDGET_OR_SCHEMA_INVALID") from None
    finally:
        history.close()


def main():
    try:
        target = target_from_base64(os.environ.get("RC6_CENSUS_TARGET_B64", ""))
        result = census(OBSERVER_DB, HISTORY_DB, target)
    except (CensusError, sqlite3.Error) as error:
        reason = str(error) if isinstance(error, CensusError) else "CENSUS_DB_READ_UNAVAILABLE"
        result = {"schema": "rc6.history.identity_day.readonly.v1",
                  "state": "NO_VERIFICADO", "reason": reason,
                  "history_v2_writes": 0, "ready_implication": "NONE"}
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
