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
    """Require an aware recorded clock, not freshness or provider attestation."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        observed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return observed.tzinfo is not None and observed.utcoffset() is not None


def underlying_family_index(rows):
    """Require one exact PPI ARS/immediate underlying, not just a family."""
    from bu_instrument_catalog import ppi_option_full_key_evidence_reason

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
        # Preserve conflicting primary coordinates before rejecting invalid
        # evidence; filtering first could make a ticker appear unique.
        identities[ticker].add((family, _upper(row.get("currency")),
                                _upper(row.get("settlement"))))
        if (raw.get("_contract_conflicts")
                or ppi_option_full_key_evidence_reason({**row, "raw": raw})
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


def current_option_contract_reason(record, underlying_rows, contract, *, checked_at):
    """Validate current PAPER inputs without rederiving READY or rewriting history.

    An old valid static snapshot is retained. It cannot authorize a new entry
    when the current PPI identity/description or its underlying is unverified.
    This checks recorded evidence, not physical provider authenticity or Book1.
    """
    from bs_instrument_contracts import aware_datetime
    from bu_instrument_catalog import (ppi_option_full_key_evidence_reason,
                                       _candidate_timestamp_is_fresh,
                                       PAPER_PPI_IDENTITY_LKG_SECONDS, contract_for)
    from rc6_contract_bridge import hard_blocked_bridge

    def raw_of(row):
        value = row.get("raw")
        return value if isinstance(value, dict) else _raw(row)

    def current(row):
        raw = raw_of(row)
        return (row.get("status") == "AVAILABLE"
                and not ppi_option_full_key_evidence_reason({**row, "raw": raw})
                and _has_recorded_observation_clock(row.get("last_seen_at"))
                and _candidate_timestamp_is_fresh(
                    row.get("last_seen_at"), checked_at, PAPER_PPI_IDENTITY_LKG_SECONDS)
                and not raw.get("_contract_conflicts")
                and not hard_blocked_bridge(raw.get("_contract_bridge"))
                and not raw.get("revoked") and not raw.get("adjusted_series_unverified")
                and not any(raw.get(key) is False for key in ("operable", "isActive", "isTradable")))

    try:
        if (record.get("instrument_type") != "OPCIONES"
                or record.get("capability") != "READY_PAPER_OPTION_LONG" or not current(record)):
            return "OPTION_CURRENT_IDENTITY_UNVERIFIED"
        raw = raw_of(record)
        terms = parse_ppi_option_description(raw.get("description"))
        if not terms or terms != parse_ppi_option_description(record.get("description")):
            return "OPTION_CURRENT_DESCRIPTION_UNVERIFIED"
        # Store rows retain metadata_json; lookup's normalized record retains
        # raw. Map the existing payload, never synthesize provider fields.
        rows = [{**row, "metadata_json": raw_of(row)} for row in underlying_rows]
        families = underlying_family_index(rows)
        family = families.get(terms["underlying"])
        if not family:
            return "OPTION_CURRENT_UNDERLYING_UNVERIFIED"
        selected = [row for row in rows if (
            _upper(row.get("ticker")) == terms["underlying"]
            and _upper(row.get("instrument_type")) == family
            and _upper(row.get("market")) == "BYMA"
            and _upper(row.get("currency")) == "ARS"
            and _upper(row.get("settlement")) == "INMEDIATA"
            and _upper(raw_of(row).get("_discovery_source")) == "PPI_PRIMARY")]
        if (len(selected) != 1 or selected[0].get("capability") != "READY_PAPER_SPOT"
                or not current(selected[0])):
            return "OPTION_CURRENT_UNDERLYING_UNVERIFIED"
        stored = contract_for(record)
        key = tuple(record[name] for name in ("ticker", "instrument_type", "market", "currency", "settlement"))
        for spec in (stored, contract):
            if (spec is None or spec.key != key or spec.currency != "ARS"
                    or spec.market != "BYMA" or spec.settlement != "INMEDIATA"
                    or _upper(spec.underlying) != terms["underlying"]
                    or spec.option_right != terms["option_right"]
                    or spec.strike != Decimal(terms["strike"])
                    or aware_datetime(spec.expires_at) != aware_datetime(terms["expires_at"])
                    or spec.cash_multiplier != Decimal(OPTION_LOT_BY_UNDERLYING_FAMILY[family])):
                return "OPTION_CURRENT_CONTRACT_MISMATCH"
        if (stored.quantity_step != contract.quantity_step
                or stored.minimum_quantity != contract.minimum_quantity):
            return "OPTION_CURRENT_CONTRACT_MISMATCH"
        return ""
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return "OPTION_CURRENT_EVIDENCE_UNVERIFIED"


def standard_long_option_evidence(row, underlying_families):
    """Build exchange-backed terms for the long-premium PAPER path only."""
    from bu_instrument_catalog import ppi_option_full_key_evidence_reason

    raw = _raw(row)
    if (row.get("status") != "AVAILABLE"
            or _upper(row.get("instrument_type")) != "OPCIONES"
            or str(row.get("capability") or "") != "NEEDS_OPTION_CONTRACT"
            or _upper(row.get("market")) != "BYMA"
            or _upper(row.get("currency")) != "ARS"
            or _upper(row.get("settlement")) != "INMEDIATA"
            or _upper(raw.get("_discovery_source")) != "PPI_PRIMARY"
            or ppi_option_full_key_evidence_reason({**row, "raw": raw})
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
