"""Fail-closed standard BYMA option contract derivation from exact PPI identity.

This module does not parse the option ticker and does not create broker terms.
It accepts only a strict PPI description plus an unambiguous current PPI
underlying family. Exchange lot and lifecycle terms come from BYMA's current
2026 option contract. Porota quantity min/step are PAPER simulation policy.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
import json
import re
from zoneinfo import ZoneInfo


BYMA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
OPTION_DESCRIPTION = re.compile(
    r"^Opción (?P<side>compra|venta) (?P<underlying>[A-Z0-9.]+) "
    r"AR\$ (?P<strike>[0-9]+(?:\.[0-9]+)?) Vto\. "
    r"(?P<expiry>[0-9]{2}/[0-9]{2}/[0-9]{4})$"
)
OPTION_LOT_BY_UNDERLYING_FAMILY = {"ACCIONES": "100", "CEDEARS": "10"}
OPTION_DERIVED_SOURCE_REF = (
    "PPI_SEARCH_INSTRUMENT_DESCRIPTION+BYMA_OPTION_CONTRACT_2026+"
    "POROTA_PAPER_ONE_CONTRACT_POLICY:v1"
)
MIN_NO_ORDINARY_DIVIDEND_ADJUSTMENT_EXPIRY = datetime(2026, 7, 1).date()


def _raw(row):
    value = row.get("metadata_json")
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _upper(value):
    return str(value or "").strip().upper()


def _has_recorded_observation_clock(value):
    """Require an aware observation clock; this does not certify freshness."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        observed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return observed.tzinfo is not None and observed.utcoffset() is not None


def underlying_family_index(rows):
    """Derive one option-eligible PPI identity; reject ticker ambiguity.

    A known PPI family is insufficient: a USD or deferred-settlement record
    cannot establish the underlying for an ARS/INMEDIATA BYMA option. This is
    historical contract evidence, not a live quote or provider PIT attestation.
    """
    identities = defaultdict(set)
    invalid = set()
    for row in rows:
        raw = _raw(row)
        family = _upper(row.get("instrument_type"))
        if (row.get("status") != "AVAILABLE"
                or _upper(raw.get("_discovery_source")) != "PPI_PRIMARY"
                or family not in OPTION_LOT_BY_UNDERLYING_FAMILY
                or _upper(row.get("market")) != "BYMA"
                or not _upper(row.get("ticker"))):
            continue
        ticker = _upper(row["ticker"])
        identities[ticker].add((
            family, _upper(row.get("currency")),
            _upper(row.get("settlement")),
        ))
        if (raw.get("_contract_conflicts")
                or _upper(row.get("settlement_source")) != "PPI_FIELD"
                or not _has_recorded_observation_clock(row.get("last_seen_at"))):
            invalid.add(ticker)
    return {
        ticker: next(iter(values))[0]
        for ticker, values in identities.items()
        if len(values) == 1 and ticker not in invalid
        and next(iter(values))[1:] == ("ARS", "INMEDIATA")
    }


def parse_ppi_option_description(description):
    """Return only terms explicitly encoded by the exact PPI description."""
    match = OPTION_DESCRIPTION.fullmatch(str(description or "").strip())
    if not match:
        return None
    try:
        strike = Decimal(match.group("strike"))
        expiry_day = datetime.strptime(match.group("expiry"), "%d/%m/%Y").date()
    except (InvalidOperation, ValueError):
        return None
    if not strike.is_finite() or strike <= 0:
        return None
    # BYMA stopped ordinary-dividend adjustments starting with JUL-26 series.
    # Extraordinary events remain subject to the existing evidence/change guard.
    if expiry_day < MIN_NO_ORDINARY_DIVIDEND_ADJUSTMENT_EXPIRY:
        return None
    expiry = datetime.combine(expiry_day, time(15, 30), tzinfo=BYMA_TZ)
    return {
        "underlying": _upper(match.group("underlying")),
        "strike": format(strike.normalize(), "f"),
        "option_right": "CALL" if match.group("side") == "compra" else "PUT",
        "expires_at": expiry.isoformat(),
    }


def standard_long_option_evidence(row, underlying_families):
    """Build exchange-backed terms for the long-premium PAPER path only."""
    raw = _raw(row)
    if (row.get("status") != "AVAILABLE"
            or _upper(row.get("instrument_type")) != "OPCIONES"
            or str(row.get("capability") or "") != "NEEDS_OPTION_CONTRACT"
            or _upper(row.get("market")) != "BYMA"
            or _upper(row.get("currency")) != "ARS"
            or _upper(row.get("settlement")) != "INMEDIATA"
            or _upper(raw.get("_discovery_source")) != "PPI_PRIMARY"
            or _upper(row.get("settlement_source")) != "PPI_FIELD"
            or not _has_recorded_observation_clock(row.get("last_seen_at"))
            or raw.get("_contract_conflicts")):
        return None
    terms = parse_ppi_option_description(row.get("description"))
    if not terms:
        return None
    underlying_family = underlying_families.get(terms["underlying"])
    lot = OPTION_LOT_BY_UNDERLYING_FAMILY.get(underlying_family)
    if not lot:
        return None
    return {
        "cash_multiplier": lot,
        "paper_quantity_min": "1",
        "paper_quantity_step": "1",
        "broker_minimum_quantity": "NO_VERIFICADO",
        "broker_quantity_step": "NO_VERIFICADO",
        "premium_basis": "PER_UNDERLYING_UNIT",
        **terms,
        "underlying_family": underlying_family,
        "premium_settlement": "T+0",
        "exercise_settlement": "T+1",
        "holder_margin_policy": "FULL_PREMIUM_MAX_LOSS",
        "writer_margin_scope": "NOT_APPLICABLE_TO_LONG_OPTION_PATH",
        "paper_position_scope": "OPTION_LONG_ONLY",
        "ordinary_dividend_adjustment_policy": "NO_ADJUSTMENT_FROM_JUL_2026",
        "extraordinary_event_guard": "CONTRACT_EVIDENCE_CHANGE_REVIEW",
        "policy_scope": "PRODUCTION_PAPER_SIMULATION_ONLY",
        "readiness_guard": (
            "EXACT_PPI_OPTION_IDENTITY+STRICT_PPI_DESCRIPTION+"
            "UNIQUE_CURRENT_PPI_UNDERLYING+BYMA_STANDARD_LOT"
        ),
    }
