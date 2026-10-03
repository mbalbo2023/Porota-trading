"""Exact PPI -> A3 standard DLR contract policy for PAPER futures.

Scope is deliberately narrow: standard monthly DLR 2026 identities only.
No spread/suffix variant is inferred, no broker margin is invented, and no
real-order route is exposed. Contract size/quotation/financial settlement and
the 2026 DLR expiry calendar come from current A3 documentation. Porota's
100% notional reserve is an internal PAPER policy, not a broker margin claim.
"""
from __future__ import annotations

from datetime import datetime, time
import json
import re
from zoneinfo import ZoneInfo

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
PAPER_OPEN = time(10, 0)
PAPER_CLOSE = time(15, 0)
PAPER_NO_ENTRY_MINUTES = 30
PAPER_EXIT_MINUTES = 10


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


def paper_session_state(at, contract=None):
    """Conservative A3 DLR PAPER window; caller still requires fresh PPI book."""
    value = at if isinstance(at, datetime) else datetime.fromisoformat(
        str(at).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("FUTURES_SESSION_TIME_NAIVE")
    local = value.astimezone(A3_TZ)
    if local.weekday() >= 5:
        return "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"
    if contract is not None:
        expiry = datetime.fromisoformat(str(contract.expires_at).replace("Z", "+00:00"))
        if local >= expiry.astimezone(A3_TZ):
            return "FUTURE_EXPIRED"
    current = local.time().replace(tzinfo=None)
    if not PAPER_OPEN <= current < PAPER_CLOSE:
        return "OUTSIDE_FUTURES_PAPER_WINDOW"
    return ""


def admission_error(at, contract):
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
    return (local.date() >= expiry.date()
            or local.hour * 60 + local.minute >= cutoff_minutes)


def assert_paper_only_invariants():
    assert STANDARD_DLR.fullmatch("DLR/OCT26")
    assert not STANDARD_DLR.fullmatch("DLR/OCT26M")
    assert not STANDARD_DLR.fullmatch("DLR/OCT26-DLR/NOV26")
    assert not STANDARD_DLR.fullmatch("DLR/ENE27")
