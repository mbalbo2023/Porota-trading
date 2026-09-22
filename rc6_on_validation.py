"""Fail-closed composition of PPI operational evidence and IOL contract analytics.

PPI remains authoritative for the operational instrument identity, executable
quote, market and settlement. IOL supplies complementary contract terms and
cash-flow analytics that PPI may not expose. This module performs no network
calls, changes no decisions, and never authorizes or sends an order.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

PRIMARY_SOURCE = "PPI"
COMPLEMENTARY_SOURCE = "IOL"
AUTHORITY = "PPI_PRIMARY_IOL_COMPLEMENTARY"

# PPI proves the operation that would be simulated. It does not have to repeat
# IOL contract fields such as ISIN, lot size, coupon schedule or duration.
_REQUIRED_PPI = (
    "symbol", "market", "currency", "settlement", "source",
    "observed_at", "price", "price_basis",
)
# IOL contributes the contract and cash-flow model. Settlement is optional
# here because the operational settlement convention belongs to PPI.
_REQUIRED_IOL = (
    "symbol", "market", "currency", "source", "observed_at",
    "units_per_lot", "isin", "clean_price", "dirty_price",
    "accrued_interest", "residual_value", "maturity", "cash_flows",
    "yield_to_maturity", "modified_duration",
)


def _decimal(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _missing(value):
    return value is None or value == ""


def validate_on_evidence(
    ppi: dict | None,
    iol: dict | None,
    *,
    price_tolerance_pct=None,
    max_ppi_age_seconds=120,
    max_snapshot_skew_seconds=300,
) -> dict:
    """Compose one matched observation; every absent required input blocks.

    READY_SHADOW means the supplied PPI operation fields and IOL contract model
    are complete enough for an observation-only simulation. It never enables
    execution or changes the operational universe.
    """
    if not isinstance(ppi, dict) or not isinstance(iol, dict):
        return {
            "state": "BLOCKED",
            "reason": "MISSING_SOURCE_PAYLOAD",
            "mismatches": ["MISSING_SOURCE_PAYLOAD"],
            "decision_effect": "OBSERVE_ONLY",
            "authority": AUTHORITY,
            "primary_source": PRIMARY_SOURCE,
            "complementary_source": COMPLEMENTARY_SOURCE,
            "simulation_inputs": None,
        }

    mismatches = []
    tolerance = _decimal(price_tolerance_pct)
    if tolerance is None or tolerance < 0:
        mismatches.append("PRICE_TOLERANCE_POLICY_REQUIRED")

    for source_name, payload, required in (
        (PRIMARY_SOURCE, ppi, _REQUIRED_PPI),
        (COMPLEMENTARY_SOURCE, iol, _REQUIRED_IOL),
    ):
        for key in required:
            value = payload.get(key)
            if _missing(value) or (key == "cash_flows" and not value):
                mismatches.append(f"MISSING_{source_name}_{key.upper()}")
        actual_source = str(payload.get("source") or "").strip().upper()
        if not actual_source.startswith(source_name):
            mismatches.append(f"WRONG_SOURCE_{source_name}")
        if _instant(payload.get("observed_at")) is None:
            mismatches.append(f"INVALID_{source_name}_OBSERVED_AT")

    # These shared identifiers establish that IOL's contract belongs to the
    # PPI instrument. Fields available only from IOL are deliberately not
    # demanded from PPI.
    for key in ("symbol", "market", "currency"):
        left = str(ppi.get(key) or "").strip().upper()
        right = str(iol.get(key) or "").strip().upper()
        if left and right and left != right:
            mismatches.append(f"IDENTITY_MISMATCH_{key.upper()}")

    # If IOL reports its settlement convention, preserve and compare it; PPI's
    # value remains the operational authority.
    ppi_settlement = str(ppi.get("settlement") or "").strip().upper()
    iol_settlement = str(iol.get("settlement") or "").strip().upper()
    if ppi_settlement and iol_settlement and ppi_settlement != iol_settlement:
        mismatches.append("SETTLEMENT_MISMATCH_PPI_IOL")

    ppi_time = _instant(ppi.get("observed_at"))
    iol_time = _instant(iol.get("observed_at"))
    if ppi_time is not None and iol_time is not None:
        # Normalize both instants to UTC before comparison, including DST.
        ppi_utc = ppi_time.timestamp()
        iol_utc = iol_time.timestamp()
        if abs(ppi_utc - iol_utc) > max_snapshot_skew_seconds:
            mismatches.append("SNAPSHOT_TIME_SKEW")
        age = max(0.0, datetime.now(ppi_time.tzinfo).timestamp() - ppi_utc)
        if age > max_ppi_age_seconds:
            mismatches.append("PPI_SNAPSHOT_STALE")

    ppi_price = _decimal(ppi.get("price"))
    ppi_basis = str(ppi.get("price_basis") or "").strip().upper()
    if ppi_price is None or ppi_price <= 0:
        mismatches.append("PPI_PRICE_INVALID")
    if ppi_basis not in {"CLEAN", "DIRTY"}:
        mismatches.append("PPI_PRICE_BASIS_UNVERIFIED")

    iol_clean = _decimal(iol.get("clean_price"))
    iol_dirty = _decimal(iol.get("dirty_price"))
    iol_accrued = _decimal(iol.get("accrued_interest"))
    iol_residual = _decimal(iol.get("residual_value"))
    for key, value in (
        ("CLEAN_PRICE", iol_clean),
        ("DIRTY_PRICE", iol_dirty),
        ("ACCRUED_INTEREST", iol_accrued),
        ("RESIDUAL_VALUE", iol_residual),
        ("UNITS_PER_LOT", _decimal(iol.get("units_per_lot"))),
        ("YIELD_TO_MATURITY", _decimal(iol.get("yield_to_maturity"))),
        ("MODIFIED_DURATION", _decimal(iol.get("modified_duration"))),
    ):
        if value is None:
            mismatches.append(f"IOL_{key}_INVALID")
    if iol_clean is not None and iol_dirty is not None and iol_accrued is not None:
        if abs((iol_clean + iol_accrued) - iol_dirty) > Decimal("0.02"):
            mismatches.append("IOL_CLEAN_DIRTY_ACCRUED_INCONSISTENT")

    maturity = str(iol.get("maturity") or "").strip()
    if not maturity:
        mismatches.append("IOL_MATURITY_INVALID")
    flows = iol.get("cash_flows")
    if not isinstance(flows, (list, tuple)) or not flows:
        mismatches.append("IOL_CASH_FLOWS_INVALID")
    if iol_residual is not None and iol_residual <= 0:
        mismatches.append("IOL_RESIDUAL_VALUE_INVALID")
    units_per_lot = _decimal(iol.get("units_per_lot"))
    if units_per_lot is not None and units_per_lot <= 0:
        mismatches.append("IOL_UNITS_PER_LOT_INVALID")

    # Reconcile only the PPI quote basis to the corresponding IOL contract
    # price. Do not compare PPI against IOL-only fields it does not publish.
    iol_comparable = iol_clean if ppi_basis == "CLEAN" else iol_dirty if ppi_basis == "DIRTY" else None
    if ppi_price is not None and iol_comparable is not None and tolerance is not None and tolerance >= 0:
        denominator = abs(iol_comparable) if iol_comparable else Decimal("1")
        difference_pct = abs(ppi_price - iol_comparable) / denominator * Decimal("100")
        if difference_pct > tolerance:
            mismatches.append("PPI_QUOTE_IOL_CONTRACT_PRICE_MISMATCH")

    # A mismatching or unknown provider basis is visible in the result. This
    # module never converts it into a buy/sell signal.
    mismatches = list(dict.fromkeys(mismatches))
    ready = not mismatches
    simulation_inputs = None
    if ready:
        simulation_inputs = {
            "operational": {
                "source": PRIMARY_SOURCE,
                "symbol": ppi["symbol"],
                "market": ppi["market"],
                "currency": ppi["currency"],
                "settlement": ppi["settlement"],
                "price": str(ppi_price),
                "price_basis": ppi_basis,
                "observed_at": ppi["observed_at"],
            },
            "contract": {
                "source": COMPLEMENTARY_SOURCE,
                "isin": iol["isin"],
                "units_per_lot": str(units_per_lot),
                "clean_price": str(iol_clean),
                "dirty_price": str(iol_dirty),
                "accrued_interest": str(iol_accrued),
                "residual_value": str(iol_residual),
                "maturity": maturity,
                "cash_flows": iol["cash_flows"],
                "yield_to_maturity": str(_decimal(iol["yield_to_maturity"])),
                "modified_duration": str(_decimal(iol["modified_duration"])),
                "observed_at": iol["observed_at"],
            },
            "decision_effect": "OBSERVE_ONLY",
        }

    return {
        "state": "READY_SHADOW" if ready else "BLOCKED",
        "reason": "COMPLEMENTARY_EVIDENCE_ALIGNED" if ready else "; ".join(mismatches),
        "mismatches": mismatches,
        "decision_effect": "OBSERVE_ONLY",
        "authority": AUTHORITY,
        "primary_source": PRIMARY_SOURCE,
        "complementary_source": COMPLEMENTARY_SOURCE,
        "simulation_inputs": simulation_inputs,
    }
