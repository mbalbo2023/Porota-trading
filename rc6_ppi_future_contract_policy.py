"""Exact PPI -> A3 standard DLR contract policy for PAPER futures.

Scope is deliberately narrow: standard monthly DLR 2026 identities only.
No spread/suffix variant is inferred, no broker margin is invented, and no
real-order route is exposed. Contract size/quotation/financial settlement and
the 2026 DLR expiry calendar come from current A3 documentation. Porota's
100% notional reserve is an internal PAPER policy, not a broker margin claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
import hashlib
import json
import re
from typing import Mapping
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime, decimal_value, utc_microseconds
from rc6_multisource_discovery import canonical_market, canonical_settlement


A3_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
STANDARD_DLR = re.compile(
    r"^DLR/(?P<month>ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC)26$"
)
DLR_2026_EXPIRY_DAYS = {
    "ENE": "2026-01-30",
    "FEB": "2026-02-27",
    "MAR": "2026-03-31",
    "ABR": "2026-04-30",
    "MAY": "2026-05-29",
    "JUN": "2026-06-30",
    "JUL": "2026-07-31",
    "AGO": "2026-08-31",
    "SEP": "2026-09-30",
    "OCT": "2026-10-30",
    "NOV": "2026-11-30",
    "DIC": "2026-12-30",
}
A3_DLR_SOURCE_REF = (
    "A3_DOLAR_PRODUCT_CURRENT+A3_FUTURES_OPTIONS_CALENDAR_2026+"
    "POROTA_PAPER_FULL_NOTIONAL_RESERVE:v1"
)
A3_DLR_PRICE_TICK_SOURCE = (
    "A3:Guia de Producto FyO DOLAR [VIGENTE]:p4:2.b:"
    "sha256:11c8a2ac9cc2b050bee36c70c8c5f95e8007bc21ec1d6d41984bbdf99ee0c506"
)
# This is the documented retrieval instant, not a guessed publication date.
A3_DLR_PRICE_TICK_KNOWN_AT = "2026-10-04T23:01:53+00:00"
PAPER_OPEN = time(10, 0)
PAPER_CLOSE = time(15, 0)
PAPER_NO_ENTRY_MINUTES = 30
PAPER_EXIT_MINUTES = 10
# A3 publishes its FyO 2026 holiday/expiry calendar, but publication of a
# yearly PDF does not prove that no subsequent special circular changed a
# particular trading session. BCRA's updated 2026 *banking* holiday table is
# an independent, conservative PAPER no-entry floor for Argentine closures;
# it is NOT a claim that every such day was separately authenticated as an A3
# exchange closure. This suppresses entries, never Deploy V2 or CI 24x7.
# A3 primary: https://a3mercados.com.ar/wp-content/uploads/2025/12/Calendario-Futuros-y-Opciones-2026-2-2.pdf
# BCRA primary: https://www.bcra.gob.ar/consulta-feriados-bancarios-2026/
# BCRA listed the 09/10-Nov special dates in the 01-Oct-2026 update.
_BCRA_2026_CONSERVATIVE_NO_ENTRY = frozenset({
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-03-23", "2026-03-24",
    "2026-04-02", "2026-04-03", "2026-05-01", "2026-05-25", "2026-06-15",
    "2026-07-09", "2026-07-10", "2026-08-17", "2026-10-12",
    "2026-11-09", "2026-11-10", "2026-11-23", "2026-12-07",
    "2026-12-08", "2026-12-25",
})
EXTERNAL_EVIDENCE_SCHEMA = "rc6.a3.dlr.external-evidence-review.v1"
MAX_EXTERNAL_EVIDENCE_BYTES = 262_144
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_EXTERNAL_AUTHORITY = frozenset({
    "real_order_id", "broker_order_id", "cash_posted", "margin_posted",
    "paper_settlement_authorized", "paper_margin_authorized", "real_routes_used",
})
PAPER_ONLY = True
ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()


@dataclass(frozen=True)
class DlrExternalEvidenceReview:
    """Offline source/PIT diagnostic, never clearing, margin or exit rights."""

    status: str
    reason_code: str
    identity_matched: bool = False
    source_kind: str | None = None
    paper_only: bool = True
    entry_authority: bool = False
    paper_settlement_authorized: bool = False
    paper_margin_authorized: bool = False
    real_routes_used: tuple = ()


def _external_hold(reason):
    return DlrExternalEvidenceReview("BLOQUEADO", reason)


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


def standard_dlr_terms(ticker):
    """Return only the exact standard monthly 2026 DLR series we can prove."""
    match = STANDARD_DLR.fullmatch(_upper(ticker))
    if not match:
        return None
    day = DLR_2026_EXPIRY_DAYS.get(match.group("month"))
    if not day:
        return None
    # A3 publishes the regular DLR trading window through 15:00 ART. The
    # timestamp is an execution cutoff in PAPER, not a claim about settlement.
    expiry = datetime.combine(datetime.fromisoformat(day).date(), PAPER_CLOSE, A3_TZ)
    return {
        "expires_at": expiry.isoformat(),
        "underlying": "DOLAR_A3500",
        "price_tick": "0.5",
        "price_tick_source": A3_DLR_PRICE_TICK_SOURCE,
        "price_tick_known_at": A3_DLR_PRICE_TICK_KNOWN_AT,
        "price_tick_effective_at": None,
    }


def standard_dlr_future_evidence(row):
    """Build a contract only from exact current PPI identity + A3 static rules."""
    raw = _raw(row)
    if (row.get("status") != "AVAILABLE"
            or _upper(row.get("instrument_type")) != "FUTUROS"
            or str(row.get("capability") or "") != "NEEDS_FUTURES_MARGIN_AND_CONTRACT"
            or canonical_market(row.get("market")) != "A3"
            or _upper(row.get("currency")) != "ARS"
            or canonical_settlement(row.get("settlement"), "FUTUROS") != "INMEDIATA"
            or _upper(raw.get("_discovery_source")) != "PPI_PRIMARY"
            or raw.get("_contract_conflicts")):
        return None
    terms = standard_dlr_terms(row.get("ticker"))
    if not terms:
        return None
    return {
        "cash_multiplier": "1000",
        "paper_quantity_min": "1",
        "paper_quantity_step": "1",
        "broker_minimum_quantity": "NO_VERIFICADO",
        "broker_quantity_step": "NO_VERIFICADO",
        **terms,
        "contract_size": "USD 1000",
        "quotation_basis": "ARS_PER_USD",
        "settlement_type": "FINANCIAL_CASH_SETTLEMENT",
        "series_policy": "STANDARD_MONTHLY_DLR_2026_ONLY",
        "price_tick_historical_effectivity": "NO_VERIFICADO",
        "settlement_price_rule": "REQUIRES_DESIGNATED_SOURCE_RULE_PER_SERIES_AND_DATE",
        "broker_calendar_halts_limits": "NO_VERIFICADO",
        "paper_margin_policy": "CONSERVATIVE_NOTIONAL_RATE",
        "paper_margin_rate": "1",
        "broker_margin_requirement": "NO_VERIFICADO_DYNAMIC",
        "paper_side_scope": "LONG_ONLY_V1",
        "policy_scope": "PRODUCTION_PAPER_SIMULATION_ONLY",
        "readiness_guard": (
            "EXACT_CURRENT_PPI_PRIMARY_IDENTITY+STANDARD_DLR_2026+"
            "A3_STATIC_CONTRACT+FULL_NOTIONAL_PAPER_RESERVE"
        ),
    }


def review_dlr_external_reference(row, reference, *, source_bytes, as_of):
    """Replay a claimed A3/clearing notice against exact, known DLR terms.

    Even matching source bytes and point-in-time fields cannot authenticate a
    provider or establish settlement/margin rights: mocks, archived documents,
    and real notices all remain ``NO_VERIFICADO`` until the separate release
    and accounting owners provide their own authorizations. No ledger access.
    Unknown 2027/variant series are NEVER promoted by this diagnostic.
    """
    try:
        at = aware_datetime(as_of)
    except (ValueError, TypeError, OverflowError):
        return _external_hold("FUTURE_EVIDENCE_AS_OF_INVALID")
    terms = standard_dlr_future_evidence(row) if isinstance(row, Mapping) else None
    if terms is None:
        return _external_hold("FUTURE_SERIES_OR_PPI_IDENTITY_UNVERIFIED")
    # Static price-grid evidence only became available at its retrieval time;
    # replaying an earlier date may not borrow that future knowledge.
    if utc_microseconds(at) < utc_microseconds(terms["price_tick_known_at"]):
        return _external_hold("FUTURE_STATIC_TERMS_NOT_KNOWN_AT_PIT")
    if not isinstance(reference, Mapping) or reference.get("schema") != EXTERNAL_EVIDENCE_SCHEMA:
        return _external_hold("FUTURE_EXTERNAL_EVIDENCE_SCHEMA_UNVERIFIED")
    if any(name in reference for name in _FORBIDDEN_EXTERNAL_AUTHORITY):
        return _external_hold("FUTURE_REFERENCE_ECONOMIC_AUTHORITY_FORBIDDEN")
    if (reference.get("source") != "A3_ARGENTINA_CLEARING_OFFICIAL"
            or not isinstance(reference.get("source_reference"), str)
            or not reference["source_reference"].strip()):
        return _external_hold("FUTURE_EXTERNAL_SOURCE_UNVERIFIED")
    digest = reference.get("document_sha256")
    if (not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            or not isinstance(source_bytes, bytes)
            or not 0 < len(source_bytes) <= MAX_EXTERNAL_EVIDENCE_BYTES
            or hashlib.sha256(source_bytes).hexdigest() != digest):
        return _external_hold("FUTURE_EXTERNAL_SOURCE_BYTES_MISMATCH")
    identity = reference.get("identity")
    if not isinstance(identity, Mapping):
        return _external_hold("FUTURE_EXTERNAL_SERIES_IDENTITY_UNVERIFIED")
    expected = {"ticker": _upper(row.get("ticker")), "family": "FUTUROS",
                "market": "A3", "currency": "ARS", "settlement": "INMEDIATA",
                "underlying": "DOLAR_A3500", "contract_size": "USD 1000",
                "settlement_type": "FINANCIAL_CASH_SETTLEMENT"}
    if any(identity.get(name) != value for name, value in expected.items()):
        return _external_hold("FUTURE_EXTERNAL_SERIES_IDENTITY_MISMATCH")
    try:
        if (decimal_value(identity.get("cash_multiplier"), "DLR multiplier", positive=True)
                != Decimal("1000")
                or utc_microseconds(identity.get("expires_at"))
                != utc_microseconds(terms["expires_at"])):
            return _external_hold("FUTURE_EXTERNAL_CONTRACT_TERMS_MISMATCH")
        effective = utc_microseconds(reference.get("effective_at"))
        published = utc_microseconds(reference.get("published_at"))
        received = utc_microseconds(reference.get("received_at"))
    except (ValueError, TypeError, OverflowError):
        return _external_hold("FUTURE_EXTERNAL_TERMS_OR_CLOCK_INVALID")
    if any(value is None for value in (effective, published, received)):
        return _external_hold("FUTURE_EXTERNAL_TERMS_OR_CLOCK_INVALID")
    if not effective <= published <= received <= utc_microseconds(at):
        return _external_hold("FUTURE_EXTERNAL_PIT_SEQUENCE_INVALID")
    kind = reference.get("kind")
    try:
        if kind == "FINAL_SETTLEMENT_PRICE":
            if effective < utc_microseconds(terms["expires_at"]):
                return _external_hold("FUTURE_FINAL_SETTLEMENT_BEFORE_EXPIRY")
            if (reference.get("price_rule") != "PRESERVE_PUBLISHED_DECIMAL"
                    or reference.get("price_currency") != "ARS_PER_USD"):
                return _external_hold("FUTURE_SETTLEMENT_PRICE_RULE_UNVERIFIED")
            decimal_value(reference.get("official_settlement_price"),
                          "DLR official settlement", positive=True)
        elif kind == "MARGIN_SCHEDULE":
            if reference.get("margin_currency") != "ARS":
                return _external_hold("FUTURE_MARGIN_CURRENCY_UNVERIFIED")
            decimal_value(reference.get("official_margin_per_contract"),
                          "DLR official margin", positive=True)
        else:
            return _external_hold("FUTURE_EXTERNAL_EVIDENCE_KIND_UNKNOWN")
    except (ValueError, TypeError, OverflowError):
        return _external_hold("FUTURE_EXTERNAL_ECONOMIC_VALUE_INVALID")
    return DlrExternalEvidenceReview(
        "NO_VERIFICADO", "FUTURE_EXTERNAL_AUTHENTICATION_AND_CLEARING_REQUIRED",
        identity_matched=True, source_kind=kind)


def paper_session_state(at, contract=None):
    """Conservative A3 DLR PAPER window; caller still requires fresh PPI book."""
    value = at if isinstance(at, datetime) else datetime.fromisoformat(
        str(at).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("FUTURES_SESSION_TIME_NAIVE")
    local = value.astimezone(A3_TZ)
    if local.weekday() >= 5:
        return "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"
    # A weekday alone never proves an A3 trading session. Without a verified
    # per-day override, a documented Argentine banking holiday stays closed
    # for simulated DLR entries even if a stale/fake PPI book arrives.
    if local.date().isoformat() in _BCRA_2026_CONSERVATIVE_NO_ENTRY:
        return "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"
    if contract is not None:
        # A session clock is not an escape hatch for unsupported yearly,
        # spread or mini series. Exact contract policy still gates opening.
        terms = standard_dlr_terms(getattr(contract, "symbol", None))
        if (terms is None or getattr(contract, "family", None) != "FUTUROS"
                or getattr(contract, "market", None) != "A3"
                or getattr(contract, "currency", None) != "ARS"
                or getattr(contract, "settlement", None) != "INMEDIATA"
                or getattr(contract, "cash_multiplier", None) != Decimal("1000")
                or getattr(contract, "quantity_step", None) != Decimal("1")
                or getattr(contract, "minimum_quantity", None) != Decimal("1")
                or getattr(contract, "underlying", None) != "DOLAR_A3500"
                or utc_microseconds(getattr(contract, "expires_at", None))
                   != utc_microseconds(terms["expires_at"])):
            return "FUTURE_SERIES_CONTRACT_NOT_REVIEWED"
        expiry = datetime.fromisoformat(str(contract.expires_at).replace("Z", "+00:00"))
        if local >= expiry.astimezone(A3_TZ):
            return "FUTURE_EXPIRED"
    current = local.time().replace(tzinfo=None)
    if not PAPER_OPEN <= current < PAPER_CLOSE:
        return "OUTSIDE_FUTURES_PAPER_WINDOW"
    return ""


def admission_error(at, contract):
    if contract is None:
        return "FUTURE_EXACT_CONTRACT_REQUIRED"
    error = paper_session_state(at, contract)
    if error:
        return error
    value = at if isinstance(at, datetime) else datetime.fromisoformat(
        str(at).replace("Z", "+00:00"))
    local = value.astimezone(A3_TZ)
    cutoff_minutes = PAPER_CLOSE.hour * 60 + PAPER_CLOSE.minute - PAPER_NO_ENTRY_MINUTES
    if local.hour * 60 + local.minute >= cutoff_minutes:
        return "FUTURES_EOD_NO_NEW_ENTRIES"
    return ""


def exit_due(at, contract):
    value = at if isinstance(at, datetime) else datetime.fromisoformat(
        str(at).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("FUTURES_SESSION_TIME_NAIVE")
    local = value.astimezone(A3_TZ)
    expiry = datetime.fromisoformat(str(contract.expires_at).replace("Z", "+00:00")).astimezone(A3_TZ)
    cutoff_minutes = PAPER_CLOSE.hour * 60 + PAPER_CLOSE.minute - PAPER_EXIT_MINUTES
    return (local >= expiry
            or local.hour * 60 + local.minute >= cutoff_minutes)


def assert_paper_only_invariants():
    assert STANDARD_DLR.fullmatch("DLR/OCT26")
    assert not STANDARD_DLR.fullmatch("DLR/OCT26M")
    assert not STANDARD_DLR.fullmatch("DLR/OCT26-DLR/NOV26")
    assert not STANDARD_DLR.fullmatch("DLR/ENE27")
