#!/usr/bin/env python3
"""Produce the canonical sanitized RC6 runtime-evidence bundle.

The collector is deliberately independent from the legacy introspector.  It
uses only Python's standard library, opens SQLite through ``mode=ro``, asserts
``PRAGMA query_only=ON`` and writes a single atomic JSON file inside an
explicit staging root.  Missing schema or safety evidence is represented as
``INCOMPLETE``/``BLOCKED``; it is never guessed into READY.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
from typing import Any, Iterable


SCHEMA = "porota-runtime-evidence-v1"
PPI_WATCH_CONTRACT_PATH = (
    Path(__file__).resolve().parent
    / "ops/policy/host-control-plane-reconciliation-v2.json"
)
SOURCES = ("PPI", "IOL", "BYMA", "A3", "ROFEX")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_TEXT = re.compile(r"^[A-Za-z0-9 _.,:;()+\-*/=\[\]]{0,240}$")
SECRET_WORD = re.compile(
    r"(?i)(authorization|bearer|password|passwd|secret|token|api[_-]?key|private[_-]?key)"
)
ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/[A-Za-z0-9_.-]+){2,}")

CATALOG_COLUMNS = (
    "ticker", "instrument_type", "market", "currency", "settlement",
    "settlement_source", "last_seen_at", "run_id", "status", "capability",
    "metadata_json",
)
RETRY_COLUMNS = (
    "ticker", "instrument_type", "market", "currency", "settlement", "source",
    "observed_at", "state", "reason", "last_attempt_at", "attempts",
)
OBSERVER_COLUMNS = (
    "id", "mode", "process_state", "session_state", "ppi_auth", "heartbeat_at",
    "last_market_data_at", "real_orders_sent", "http_allowed", "http_blocked",
)
CANDIDATE_COLUMNS = (
    "ticker", "instrument_type", "market", "settlement", "can_simulate",
    "status", "detail", "last_checked_at",
)
SOURCE_SYNC_COLUMNS = (
    "source", "status", "last_attempt_at", "last_success_at", "items", "detail",
)
ALLOWED_TABLES = {
    "observer_state": OBSERVER_COLUMNS,
    "financial_instrument_catalog": CATALOG_COLUMNS,
    "complementary_contract_retry": RETRY_COLUMNS,
    "candidate_universe": CANDIDATE_COLUMNS,
    "source_sync": SOURCE_SYNC_COLUMNS,
}


class EvidenceError(RuntimeError):
    """Stable fail-closed error without leaking source payloads."""


_DENIED_SQLITE_ACTIONS = {
    getattr(sqlite3, name) for name in (
        "SQLITE_ATTACH", "SQLITE_DETACH", "SQLITE_INSERT", "SQLITE_UPDATE",
        "SQLITE_DELETE", "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX", "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_CREATE_VTABLE",
        "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE", "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW", "SQLITE_DROP_VTABLE",
        "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE",
    ) if hasattr(sqlite3, name)
}


def _readonly_authorizer(action: int, _arg1: str | None, _arg2: str | None,
                         _database: str | None, _trigger: str | None) -> int:
    return sqlite3.SQLITE_DENY if action in _DENIED_SQLITE_ACTIONS else sqlite3.SQLITE_OK


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def safe_text(value: Any, fallback: str = "NO_VERIFICADO") -> str:
    text = str(value or "").strip()[:240]
    if not text or SECRET_WORD.search(text) or ABSOLUTE_PATH.search(text):
        return fallback
    return text if SAFE_TEXT.fullmatch(text) else fallback


def _json_object(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing DB read-only and prove query-only enforcement."""
    if not path.is_file() or path.is_symlink():
        raise EvidenceError("DATABASE_NOT_REGULAR_FILE")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    state = conn.execute("PRAGMA query_only").fetchone()
    if not state or int(state[0]) != 1:
        conn.close()
        raise EvidenceError("QUERY_ONLY_NOT_ENFORCED")
    conn.set_authorizer(_readonly_authorizer)
    return conn


def _schema(conn: sqlite3.Connection) -> dict[str, set[str]]:
    tables = {
        str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    result: dict[str, set[str]] = {}
    for table in ALLOWED_TABLES:
        if table in tables:
            result[table] = {
                str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
            }
    return result


def _require_columns(schema: dict[str, set[str]], table: str, columns: Iterable[str]) -> list[str]:
    if table not in schema:
        return [f"MISSING_TABLE:{table}"]
    missing = sorted(set(columns) - schema[table])
    return [f"MISSING_COLUMN:{table}.{column}" for column in missing]


def _rows(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> list[dict[str, Any]]:
    if table not in ALLOWED_TABLES or tuple(columns) != ALLOWED_TABLES[table]:
        raise EvidenceError("QUERY_NOT_ALLOWLISTED")
    quoted = ",".join(f'"{column}"' for column in columns)
    return [dict(row) for row in conn.execute(f'SELECT {quoted} FROM "{table}"')]


def _freshness(value: Any, current: datetime, max_age_seconds: int) -> dict[str, Any]:
    observed = iso(value)
    if observed is None:
        return {"state": "UNKNOWN", "observed_at": None, "age_seconds": None}
    age = (current - datetime.fromisoformat(observed)).total_seconds()
    state = "FRESH" if 0 <= age <= max_age_seconds else "STALE"
    return {"state": state, "observed_at": observed, "age_seconds": round(age, 3)}


def _metadata(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _source_freshness(metadata: dict[str, Any], source: str, primary_at: Any,
                      current: datetime, max_age_seconds: int) -> dict[str, Any]:
    if source == "PPI":
        return _freshness(primary_at, current, max_age_seconds)
    by_source = metadata.get("_freshness_by_source")
    by_source = by_source if isinstance(by_source, dict) else {}
    aliases = {
        "IOL": ("IOL", "IOL_COMPLEMENTARY"),
        "BYMA": ("BYMA", "BYMA_PUBLIC_COMPLEMENTARY"),
        "A3": ("A3", "A3_PRIMARY", "A3_CEM_CLOSING"),
        "ROFEX": ("ROFEX", "MATBA_ROFEX"),
    }[source]
    stamp = next((by_source.get(name) for name in aliases if by_source.get(name)), None)
    return _freshness(stamp, current, max_age_seconds)


def _primary_identity(row: dict[str, Any], metadata: dict[str, Any]) -> bool:
    discovery = str(metadata.get("_discovery_source") or "").upper()
    availability = str(metadata.get("_availability_source") or "").upper()
    settlement_source = str(row.get("settlement_source") or "").upper()
    return (
        discovery in {"PPI_PRIMARY", "LEGACY_CATALOG"}
        or availability.startswith("PPI_PRIMARY")
        or settlement_source in {"PPI_FIELD", "REQUEST_CANDIDATE"}
    )


def _identity_key(row: dict[str, Any]) -> str:
    values = (
        row.get("ticker"), row.get("instrument_type"), row.get("market"),
        row.get("currency"), row.get("settlement"),
    )
    return "|".join(str(value or "").strip().upper() for value in values)


def _candidate_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(
        str(row.get(name) or "").strip().upper()
        for name in ("ticker", "instrument_type", "market")
    )


def _retry_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return tuple(
        str(row.get(name) or "").strip().upper()
        for name in ("ticker", "instrument_type", "market", "currency", "settlement")
    )


def _catalog_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return _retry_key(row)


def _instrument_rows(catalog: list[dict[str, Any]], retries: list[dict[str, Any]],
                     candidates: list[dict[str, Any]], current: datetime,
                     freshness_seconds: int) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    retry_by_key: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for retry in retries:
        retry_by_key[_retry_key(retry)].append(retry)
    candidate_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        candidate_by_key[_candidate_key(candidate)].append(candidate)

    symbol_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in catalog:
        symbol_groups[(str(row.get("ticker") or "").upper(),
                       str(row.get("instrument_type") or "").upper())].append(row)

    instruments: list[dict[str, Any]] = []
    for row in sorted(catalog, key=_identity_key):
        metadata = _metadata(row.get("metadata_json"))
        primary = _primary_identity(row, metadata)
        source_state = {
            source: {
                "evidence": "PRIMARY_IDENTITY" if source == "PPI" and primary else (
                    "COMPLEMENTARY" if _source_freshness(
                        metadata, source, row.get("last_seen_at"), current, freshness_seconds
                    )["observed_at"] else "NO_VERIFICADO"
                ),
                "freshness": _source_freshness(
                    metadata, source, row.get("last_seen_at"), current, freshness_seconds
                ),
            }
            for source in SOURCES
        }
        key = _catalog_key(row)
        retry_rows = retry_by_key.get(key, [])
        ambiguity = len(symbol_groups[
            (str(row.get("ticker") or "").upper(), str(row.get("instrument_type") or "").upper())
        ]) > 1
        retry_ambiguity = any("AMBIG" in str(item.get("reason") or "").upper() for item in retry_rows)
        capability = safe_text(row.get("capability"), "UNKNOWN_CAPABILITY")
        catalog_status = safe_text(row.get("status"), "UNKNOWN_STATUS")
        ppi_fresh = source_state["PPI"]["freshness"]["state"] == "FRESH"
        reasons: list[str] = []
        if not primary:
            reasons.append("PPI_PRIMARY_IDENTITY_NOT_VERIFIED")
        if not ppi_fresh:
            reasons.append("PPI_FRESHNESS_" + source_state["PPI"]["freshness"]["state"])
        if catalog_status != "AVAILABLE":
            reasons.append("CATALOG_STATUS:" + catalog_status)
        if not capability.startswith("READY_PAPER_"):
            reasons.append("CAPABILITY:" + capability)
        if ambiguity or retry_ambiguity:
            reasons.append("IDENTITY_AMBIGUOUS")

        projected = candidate_by_key.get(_candidate_key(row), [])
        if len(projected) != 1:
            reasons.append("CANDIDATE_LEDGER_CARDINALITY:" + str(len(projected)))
            issues.append("CANDIDATE_LEDGER_CARDINALITY")
        elif int(projected[0].get("can_simulate") or 0) not in {0, 1}:
            reasons.append("CANDIDATE_LEDGER_INVALID")
            issues.append("CANDIDATE_LEDGER_INVALID")
        elif int(projected[0].get("can_simulate") or 0) == 1 and reasons:
            reasons.append("CANDIDATE_LEDGER_CONTRADICTS_FAIL_CLOSED")
            issues.append("CANDIDATE_LEDGER_CONTRADICTION")

        readiness = "READY_PAPER" if not reasons else "NO_READY"
        if readiness == "READY_PAPER" and any(
            detail["freshness"]["state"] == "UNKNOWN"
            for detail in source_state.values()
            if detail["evidence"] != "NO_VERIFICADO"
        ):
            readiness = "NO_READY"
            reasons.append("KNOWN_SOURCE_FRESHNESS_UNKNOWN")

        retry_view = [
            {
                "source": safe_text(item.get("source")),
                "state": safe_text(item.get("state")),
                "attempts": int(item.get("attempts") or 0),
                "last_attempt_at": iso(item.get("last_attempt_at")),
                "observed_at": iso(item.get("observed_at")),
                "reason": safe_text(item.get("reason")),
                "success": (
                    "VERIFIED" if str(item.get("state") or "").upper()
                    in {"SUCCESS", "SUCCEEDED", "RESOLVED", "READY"}
                    else "NO_VERIFICADO"
                ),
                "error": (
                    safe_text(item.get("reason"))
                    if str(item.get("state") or "").upper()
                    not in {"SUCCESS", "SUCCEEDED", "RESOLVED", "READY"}
                    else None
                ),
            }
            for item in sorted(retry_rows, key=lambda item: str(item.get("source") or ""))
        ]
        ticker = str(row.get("ticker") or "").strip().upper()
        instrument = {
            "identity": {
                "primary_source": "PPI" if primary else "NO_VERIFICADO",
                "ticker": ticker,
                "family": str(row.get("instrument_type") or "").strip().upper(),
                "market": str(row.get("market") or "").strip().upper(),
                "currency": str(row.get("currency") or "").strip().upper(),
                "settlement": str(row.get("settlement") or "").strip().upper(),
                "identity_key": _identity_key(row),
            },
            "catalog": {
                "status": catalog_status,
                "capability": capability,
                "last_seen_at": iso(row.get("last_seen_at")),
                "run_id": safe_text(row.get("run_id")),
            },
            "readiness": {
                "status": readiness,
                "reasons": reasons or ["NO_OPEN_READINESS_GAP"],
                "paper_shadow_only": True,
                "real_money_authorized": False,
            },
            "sources": source_state,
            "retry": retry_view,
            "identity_flags": {
                "ymc_special": ticker.startswith("YMC"),
                "ambiguous": ambiguity or retry_ambiguity,
                "duplicate": ambiguity,
                "ghost": False,
            },
        }
        instruments.append(instrument)

    catalog_candidates = {_candidate_key(row) for row in catalog}
    ghosts = sorted(set(candidate_by_key) - catalog_candidates)
    if ghosts:
        issues.append(f"CANDIDATE_GHOSTS:{len(ghosts)}")
    for rows in candidate_by_key.values():
        if len(rows) != 1:
            issues.append("CANDIDATE_DUPLICATE_KEY")
            break
    return instruments, sorted(set(issues))


def _identity_anomalies(catalog: list[dict[str, Any]], candidates: list[dict[str, Any]],
                        instruments: list[dict[str, Any]]) -> dict[str, Any]:
    catalog_keys = {_candidate_key(row) for row in catalog}
    grouped = Counter(_candidate_key(row) for row in candidates)
    candidate_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        candidate_by_key[_candidate_key(row)].append(row)
    ghosts = sorted(set(grouped) - catalog_keys)
    duplicates = sorted((key, count) for key, count in grouped.items() if count > 1)
    return {
        "ghosts": [
            {
                "ticker": key[0],
                "family": key[1],
                "market": key[2],
                "settlement": safe_text(candidate_by_key[key][0].get("settlement")),
                "can_simulate": int(candidate_by_key[key][0].get("can_simulate") or 0),
                "status": safe_text(candidate_by_key[key][0].get("status")),
                "detail": safe_text(candidate_by_key[key][0].get("detail")),
                "last_checked_at": iso(candidate_by_key[key][0].get("last_checked_at")),
            }
            for key in ghosts
        ],
        "duplicates": [
            {"ticker": key[0], "family": key[1], "market": key[2], "count": count}
            for key, count in duplicates
        ],
        "ambiguous_identity_keys": sorted(
            row["identity"]["identity_key"] for row in instruments
            if row["identity_flags"]["ambiguous"]
        ),
        "ymc_identity_keys": sorted(
            row["identity"]["identity_key"] for row in instruments
            if row["identity_flags"]["ymc_special"]
        ),
    }


def _summaries(instruments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for instrument in instruments:
        grouped[instrument["identity"]["family"]].append(instrument)
    return [
        {
            "family": family,
            "instruments": len(rows),
            "ready_paper": sum(row["readiness"]["status"] == "READY_PAPER" for row in rows),
            "not_ready": sum(row["readiness"]["status"] != "READY_PAPER" for row in rows),
            "ambiguous": sum(row["identity_flags"]["ambiguous"] for row in rows),
            "ymc_special": sum(row["identity_flags"]["ymc_special"] for row in rows),
        }
        for family, rows in sorted(grouped.items())
    ]


def _delta(previous: dict[str, Any], instruments: list[dict[str, Any]]) -> dict[str, Any]:
    if previous.get("schema") != SCHEMA or not isinstance(previous.get("instruments"), list):
        return {"state": "NO_VERIFICADO", "promotions": [], "demotions": [], "changed": []}
    before = {
        str(item.get("identity", {}).get("identity_key") or ""):
        str(item.get("readiness", {}).get("status") or "")
        for item in previous["instruments"] if isinstance(item, dict)
    }
    after = {
        item["identity"]["identity_key"]: item["readiness"]["status"]
        for item in instruments
    }
    changed = [
        {"identity_key": key, "before": before.get(key, "ABSENT"), "after": after[key]}
        for key in sorted(after) if before.get(key) != after[key]
    ]
    return {
        "state": "VERIFIED",
        "promotions": [row for row in changed if row["after"] == "READY_PAPER"],
        "demotions": [row for row in changed if row["before"] == "READY_PAPER"],
        "changed": changed,
    }


def _hash_file(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _ppi_watch_contract(path: Path = PPI_WATCH_CONTRACT_PATH) -> dict[str, Any]:
    policy = _json_object(path)
    contract = policy.get("ppi_watch_evidence_contract")
    if not isinstance(contract, dict):
        raise EvidenceError("PPI_WATCH_CONTRACT_MISSING")
    if contract.get("schema_version") != 1:
        raise EvidenceError("PPI_WATCH_CONTRACT_VERSION_INVALID")
    if contract.get("mutation_allowed") is not False:
        raise EvidenceError("PPI_WATCH_MUTATION_POLICY_INVALID")
    expected = contract.get("expected_unit_names")
    if not isinstance(expected, list) or any(
        not isinstance(name, str) or not name or "/" in name for name in expected
    ):
        raise EvidenceError("PPI_WATCH_EXPECTED_UNITS_INVALID")
    if len(expected) != len(set(expected)):
        raise EvidenceError("PPI_WATCH_EXPECTED_UNITS_DUPLICATED")
    discovery = contract.get("discovery")
    tokens = discovery.get("required_name_tokens") if isinstance(discovery, dict) else None
    if not isinstance(tokens, list) or not tokens or any(
        not isinstance(token, str) or not token for token in tokens
    ):
        raise EvidenceError("PPI_WATCH_DISCOVERY_INVALID")
    return contract


def _ppi_watch_result(current: datetime, contract: dict[str, Any], *, state: str,
                      reason: str, units: list[dict[str, Any]],
                      enumeration_succeeded: bool) -> dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "checked_at": current.isoformat(),
        "contract_schema_version": contract["schema_version"],
        "owner": safe_text(contract.get("owner")),
        "systemd_presence_expectation": safe_text(
            contract.get("systemd_presence_expectation")
        ),
        "enumeration_succeeded": enumeration_succeeded,
        "units": units,
        "mutation_attempted": False,
    }


def ppi_watch_status(current: datetime, contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify PPI Watch metadata fail-closed without any systemd mutation."""
    contract = _ppi_watch_contract() if contract is None else contract
    # Validate injected test/consumer contracts through the same schema checks.
    if contract.get("schema_version") != 1 or contract.get("mutation_allowed") is not False:
        raise EvidenceError("PPI_WATCH_CONTRACT_INVALID")
    states = contract.get("states") if isinstance(contract.get("states"), dict) else {}
    discovery = contract.get("discovery") if isinstance(contract.get("discovery"), dict) else {}
    tokens = discovery.get("required_name_tokens")
    expected = contract.get("expected_unit_names")
    if not isinstance(tokens, list) or not tokens or not isinstance(expected, list):
        raise EvidenceError("PPI_WATCH_CONTRACT_INVALID")
    try:
        listing = subprocess.run(
            ["systemctl", "list-unit-files", "--no-legend", "--no-pager"],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return _ppi_watch_result(
            current, contract,
            state=str(states.get("enumeration_failure") or "NO_VERIFICADO"),
            reason="ENUMERATION_EXCEPTION", units=[], enumeration_succeeded=False,
        )
    if listing.returncode != 0:
        return _ppi_watch_result(
            current, contract,
            state=str(states.get("enumeration_failure") or "NO_VERIFICADO"),
            reason="ENUMERATION_FAILED", units=[], enumeration_succeeded=False,
        )
    case_sensitive = discovery.get("case_sensitive") is True
    required_tokens = [str(token) for token in tokens]

    def matches(name: str) -> bool:
        candidate = name if case_sensitive else name.lower()
        wanted = required_tokens if case_sensitive else [token.lower() for token in required_tokens]
        return all(token in candidate for token in wanted)

    names = sorted({
        line.split()[0] for line in listing.stdout.splitlines()
        if line.split() and matches(line.split()[0])
    })
    if not names:
        expectation = str(contract.get("systemd_presence_expectation") or "UNKNOWN")
        verified_absent = expectation == "ABSENT"
        absence_state = (
            states.get("verified_absence") or "VERIFIED_ABSENT"
            if verified_absent
            else states.get("zero_matches") or "NOT_PRESENT"
        )
        return _ppi_watch_result(
            current, contract,
            state=str(absence_state),
            reason="ABSENCE_REQUIRED_BY_CONTRACT" if verified_absent else "NO_MATCHING_UNIT",
            units=[], enumeration_succeeded=True,
        )
    if len(names) != 1:
        return _ppi_watch_result(
            current, contract,
            state=str(states.get("multiple_matches") or "AMBIGUOUS"),
            reason="MULTIPLE_MATCHING_UNITS",
            units=[{"unit": safe_text(name)} for name in names],
            enumeration_succeeded=True,
        )

    name = names[0]
    if name not in expected:
        return _ppi_watch_result(
            current, contract,
            state=str(states.get("unregistered_candidate") or "NO_VERIFICADO"),
            reason="UNIT_NOT_AUTHORIZED_BY_CONTRACT",
            units=[{"unit": safe_text(name)}], enumeration_succeeded=True,
        )

    observed: dict[str, str] = {}
    commands = {
        "enabled": ["systemctl", "is-enabled", name],
        "active": ["systemctl", "is-active", name],
        "fragment": ["systemctl", "show", name, "--property=FragmentPath", "--value"],
    }
    command_failed = False
    for key, command in commands.items():
        try:
            result = subprocess.run(
                command, check=False, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            command_failed = True
            observed[key] = ""
        else:
            observed[key] = result.stdout.strip()
            if result.returncode != 0 or not observed[key]:
                command_failed = True
    digest = _hash_file(Path(observed["fragment"])) if observed.get("fragment") else None
    unit = {
        "unit": safe_text(name),
        "enabled": safe_text(observed.get("enabled")),
        "active": safe_text(observed.get("active")),
        "unit_sha256": digest or "NO_VERIFICADO",
    }
    verified = (
        not command_failed
        and observed.get("enabled") == "enabled"
        and observed.get("active") == "active"
        and digest is not None
    )
    verification_state = (
        states.get("verified_unit") or "VERIFIED_READ_ONLY"
        if verified
        else states.get("verification_failure") or "NO_VERIFICADO"
    )
    return _ppi_watch_result(
        current, contract,
        state=str(verification_state),
        reason="EXACT_EXPECTED_UNIT_VERIFIED" if verified else "EXPECTED_UNIT_NOT_VERIFIED",
        units=[unit], enumeration_succeeded=True,
    )


def _provenance(deploy: dict[str, Any], frozen: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    def sha40(value: Any) -> str:
        text = str(value or "").lower()
        return text if SHA40.fullmatch(text) else "NO_VERIFICADO"

    def sha64(value: Any) -> str:
        text = str(value or "").lower().removeprefix("sha256:")
        return text if SHA64.fullmatch(text) else "NO_VERIFICADO"

    return {
        "candidate_sha": sha40(deploy.get("candidate_sha") or frozen.get("candidate_sha")),
        "deploy_sha": sha40(deploy.get("deploy_sha")),
        "tree_sha": sha40(frozen.get("candidate_tree_sha")),
        "image_identity": safe_text(deploy.get("image_id") or frozen.get("image_id")),
        "image_tar_sha256": sha64(frozen.get("image_tar_sha256")),
        "bundle_sha256": sha64(manifest.get("bundle_sha256")),
        "manifest_schema_version": manifest.get("schema_version", "NO_VERIFICADO"),
        "deploy_state_recorded_at": iso(deploy.get("recorded_at")),
    }


def _safety(observer: dict[str, Any], mode: dict[str, Any], deploy: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    api = mode.get("apis") if isinstance(mode.get("apis"), dict) else {}
    route_capability = str(api.get("PPI_ORDERS") or deploy.get("real_order_capability") or "NO_VERIFICADO").upper()
    route_calls = str(deploy.get("real_order_routes") or "NO_VERIFICADO").upper()
    safety = {
        "mode": safe_text(observer.get("mode")),
        "operation_mode": safe_text(mode.get("mode")),
        "execution": safe_text(mode.get("execution")),
        "real_orders_sent": int(observer.get("real_orders_sent") or 0),
        "real_order_route_capability": safe_text(route_capability),
        "real_order_routes": safe_text(route_calls),
        "paper_shadow_only": True,
    }
    blockers = []
    if safety["mode"] != "PRODUCTION_PAPER" or safety["operation_mode"] != "PRODUCTION_PAPER":
        blockers.append("NON_PAPER_MODE")
    if safety["execution"] != "SIMULATED":
        blockers.append("NON_SIMULATED_EXECUTION")
    if safety["real_orders_sent"] != 0:
        blockers.append("REAL_ORDERS_SENT_NONZERO")
    if safety["real_order_route_capability"] != "BLOCKED":
        blockers.append("REAL_ROUTE_NOT_BLOCKED")
    if safety["real_order_routes"] not in {"NOT_CALLED", "BLOCKED"}:
        blockers.append("REAL_ROUTE_CALL_STATE_UNVERIFIED")
    return safety, blockers


def build_bundle(*, db_path: Path, operation_mode_path: Path | None,
                 deploy_state_path: Path | None, frozen_path: Path | None,
                 manifest_path: Path | None, previous_path: Path | None,
                 freshness_seconds: int, include_ppi_watch: bool = True,
                 current: datetime | None = None) -> dict[str, Any]:
    current = current or utcnow()
    gaps: list[str] = []
    with connect_readonly(db_path) as conn:
        schema = _schema(conn)
        for table, columns in (
            ("observer_state", OBSERVER_COLUMNS),
            ("financial_instrument_catalog", CATALOG_COLUMNS),
            ("complementary_contract_retry", RETRY_COLUMNS),
            ("candidate_universe", CANDIDATE_COLUMNS),
            ("source_sync", SOURCE_SYNC_COLUMNS),
        ):
            gaps.extend(_require_columns(schema, table, columns))
        if gaps:
            observer_rows = _rows(conn, "observer_state", OBSERVER_COLUMNS) if not _require_columns(schema, "observer_state", OBSERVER_COLUMNS) else []
            catalog = []
            retries = []
            candidates = []
            sync_rows = []
        else:
            observer_rows = _rows(conn, "observer_state", OBSERVER_COLUMNS)
            catalog = _rows(conn, "financial_instrument_catalog", CATALOG_COLUMNS)
            retries = _rows(conn, "complementary_contract_retry", RETRY_COLUMNS)
            candidates = _rows(conn, "candidate_universe", CANDIDATE_COLUMNS)
            sync_rows = _rows(conn, "source_sync", SOURCE_SYNC_COLUMNS)
    if len(observer_rows) != 1 or int(observer_rows[0].get("id") or 0) != 1:
        gaps.append("OBSERVER_STATE_CARDINALITY")
        observer = observer_rows[0] if observer_rows else {}
    else:
        observer = observer_rows[0]
    if not catalog:
        gaps.append("CATALOG_EMPTY")

    instruments, ledger_issues = _instrument_rows(
        catalog, retries, candidates, current, freshness_seconds
    )
    gaps.extend(ledger_issues)
    if len(instruments) != len(catalog):
        gaps.append("CATALOG_LEDGER_COUNT_MISMATCH")

    operation_mode = _json_object(operation_mode_path)
    deploy = _json_object(deploy_state_path)
    frozen = _json_object(frozen_path)
    manifest = _json_object(manifest_path)
    safety, blockers = _safety(observer, operation_mode, deploy)
    if not operation_mode:
        gaps.append("OPERATION_MODE_NO_VERIFICADO")
    provenance = _provenance(deploy, frozen, manifest)
    missing_provenance = sorted(key for key, value in provenance.items() if value in {None, "NO_VERIFICADO"})
    previous = _json_object(previous_path)
    source_sync = [
        {
            "source": safe_text(row.get("source")),
            "status": safe_text(row.get("status")),
            "last_attempt_at": iso(row.get("last_attempt_at")),
            "last_success_at": iso(row.get("last_success_at")),
            "items": int(row.get("items") or 0),
            "detail": safe_text(row.get("detail")),
        }
        for row in sorted(sync_rows, key=lambda row: str(row.get("source") or ""))
    ]
    ppi_watch = ppi_watch_status(current) if include_ppi_watch else {
        "state": "NO_VERIFICADO", "reason": "CHECK_SKIPPED",
        "checked_at": current.isoformat(), "contract_schema_version": 1,
        "owner": "EXTERNAL_SEPARATE_OWNER",
        "systemd_presence_expectation": "UNKNOWN",
        "enumeration_succeeded": False, "units": [], "mutation_attempted": False,
    }
    if include_ppi_watch and ppi_watch["state"] not in {
        "VERIFIED_READ_ONLY", "VERIFIED_ABSENT"
    }:
        gaps.append("PPI_WATCH_NO_VERIFICADO")
    status = "BLOCKED" if blockers else "INCOMPLETE" if gaps else "COMPLETE"
    bundle = {
        "schema": SCHEMA,
        "schema_version": 1,
        "status": status,
        "generated_at": current.isoformat(),
        "source_runtime_timestamp": iso(observer.get("heartbeat_at")),
        "provenance": provenance,
        "provenance_missing": missing_provenance,
        "safety": safety,
        "ppi_watch": ppi_watch,
        "database": {
            "open_mode": "mode=ro",
            "query_only": True,
            "schema_allowlist": sorted(ALLOWED_TABLES),
            "catalog_rows": len(catalog),
            "candidate_rows": len(candidates),
            # Compatibility alias: unlike the previous implementation this now
            # counts the actual candidate ledger, never catalog projections.
            "ledger_rows": len(candidates),
        },
        "observer": {
            "process_state": safe_text(observer.get("process_state")),
            "session_state": safe_text(observer.get("session_state")),
            "ppi_auth": safe_text(observer.get("ppi_auth")),
            "heartbeat_at": iso(observer.get("heartbeat_at")),
            "last_market_data_at": iso(observer.get("last_market_data_at")),
        },
        "source_sync": source_sync,
        "families": _summaries(instruments),
        "identity_anomalies": _identity_anomalies(catalog, candidates, instruments),
        "instruments": instruments,
        "delta": _delta(previous, instruments),
        "gaps": sorted(set(gaps)),
        "blockers": sorted(set(blockers)),
        "publication": {
            "target_branch": "runtime-observability",
            "latest_path": "runtime/evidence/latest.json",
            "consumer_contract": "FIX_COMMIT_AND_BLOB_SHA",
        },
    }
    assert_sanitized(bundle)
    return bundle


def assert_sanitized(payload: Any, trail: tuple[str, ...] = ()) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if SECRET_WORD.search(str(key)):
                raise EvidenceError("SANITIZER_FORBIDDEN_KEY")
            assert_sanitized(value, trail + (str(key),))
    elif isinstance(payload, list):
        for item in payload:
            assert_sanitized(item, trail)
    elif isinstance(payload, str):
        if SECRET_WORD.search(payload) or ABSOLUTE_PATH.search(payload):
            raise EvidenceError("SANITIZER_FORBIDDEN_VALUE")


def validate_bundle(payload: dict[str, Any]) -> None:
    required = {
        "schema", "schema_version", "status", "generated_at", "source_runtime_timestamp", "provenance",
        "provenance_missing", "safety",
        "ppi_watch", "database", "observer", "source_sync", "families",
        "identity_anomalies", "instruments",
        "delta", "gaps", "blockers", "publication",
    }
    if set(payload) != required:
        raise EvidenceError("BUNDLE_SCHEMA_MISMATCH")
    if payload["schema"] != SCHEMA or payload["schema_version"] != 1:
        raise EvidenceError("BUNDLE_VERSION_MISMATCH")
    if payload["status"] not in {"COMPLETE", "INCOMPLETE", "BLOCKED"}:
        raise EvidenceError("BUNDLE_STATUS_INVALID")
    if payload["database"].get("open_mode") != "mode=ro" or payload["database"].get("query_only") is not True:
        raise EvidenceError("BUNDLE_READONLY_PROOF_MISSING")
    if payload["publication"].get("consumer_contract") != "FIX_COMMIT_AND_BLOB_SHA":
        raise EvidenceError("BUNDLE_PINNING_CONTRACT_MISSING")
    ppi_watch = payload["ppi_watch"]
    if not isinstance(ppi_watch, dict) or ppi_watch.get("mutation_attempted") is not False:
        raise EvidenceError("PPI_WATCH_READONLY_PROOF_MISSING")
    if ppi_watch.get("state") not in {
        "VERIFIED_READ_ONLY", "VERIFIED_ABSENT", "NOT_PRESENT",
        "AMBIGUOUS", "NO_VERIFICADO",
    }:
        raise EvidenceError("PPI_WATCH_STATE_INVALID")
    if ppi_watch.get("state") == "VERIFIED_READ_ONLY":
        units = ppi_watch.get("units")
        if (
            ppi_watch.get("enumeration_succeeded") is not True
            or not isinstance(units, list) or len(units) != 1
            or units[0].get("enabled") != "enabled"
            or units[0].get("active") != "active"
            or not SHA64.fullmatch(str(units[0].get("unit_sha256") or ""))
        ):
            raise EvidenceError("PPI_WATCH_FALSE_VERIFIED_UNIT")
    if ppi_watch.get("state") == "VERIFIED_ABSENT" and (
        ppi_watch.get("systemd_presence_expectation") != "ABSENT"
        or ppi_watch.get("enumeration_succeeded") is not True
        or ppi_watch.get("units") != []
    ):
        raise EvidenceError("PPI_WATCH_FALSE_VERIFIED_ABSENCE")
    for instrument in payload["instruments"]:
        freshness = [detail.get("freshness", {}).get("state") for detail in instrument.get("sources", {}).values()]
        if instrument.get("readiness", {}).get("status") == "READY_PAPER" and "UNKNOWN" in freshness:
            known = [detail for detail in instrument["sources"].values() if detail.get("evidence") != "NO_VERIFICADO"]
            if any(detail.get("freshness", {}).get("state") == "UNKNOWN" for detail in known):
                raise EvidenceError("UNKNOWN_FRESHNESS_READY")
    assert_sanitized(payload)


def atomic_json(output: Path, staging_root: Path, payload: dict[str, Any]) -> None:
    root = staging_root.resolve()
    target = output.resolve()
    if target != root and root not in target.parents:
        raise EvidenceError("OUTPUT_OUTSIDE_STAGING_ROOT")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".runtime-evidence-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--operation-mode", type=Path)
    parser.add_argument("--deploy-state", type=Path)
    parser.add_argument("--frozen-candidate", type=Path)
    parser.add_argument("--artifact-manifest", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freshness-seconds", type=int, default=86400)
    parser.add_argument("--skip-ppi-watch", action="store_true")
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args(argv)
    if args.validate:
        validate_bundle(_json_object(args.validate))
        print("POROTA_RUNTIME_EVIDENCE_VALID=GREEN")
        return 0
    if args.freshness_seconds < 1:
        raise SystemExit("FRESHNESS_SECONDS_INVALID")
    bundle = build_bundle(
        db_path=args.db,
        operation_mode_path=args.operation_mode,
        deploy_state_path=args.deploy_state,
        frozen_path=args.frozen_candidate,
        manifest_path=args.artifact_manifest,
        previous_path=args.previous,
        freshness_seconds=args.freshness_seconds,
        include_ppi_watch=not args.skip_ppi_watch,
    )
    validate_bundle(bundle)
    atomic_json(args.output, args.staging_root, bundle)
    print(json.dumps({
        "status": bundle["status"],
        "schema": bundle["schema"],
        "catalog_rows": bundle["database"]["catalog_rows"],
        "candidate_rows": bundle["database"]["candidate_rows"],
        "ledger_rows": bundle["database"]["ledger_rows"],
        "blockers": bundle["blockers"],
        "gaps": bundle["gaps"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
