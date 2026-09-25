"""IOL -> Contract Evidence v2 normalization for RC6.

Read-only provider payloads are converted to canonical contract fields. Missing
semantics stay missing. This module has no MCP transport and no order methods.
"""
from __future__ import annotations
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

TERM_MAP={"T0":"INMEDIATA","T1":"A-24HS","T2":"A-48HS","T3":"A-72HS"}
MARKET_MAP={"BCBA":"BYMA","BYMA":"BYMA","ROFEX":"ROFEX","A3":"A3"}

def market(value):
    key=str(value or "").strip().upper()
    return MARKET_MAP.get(key,key)

def settlement(value):
    key=str(value or "").strip().upper()
    return TERM_MAP.get(key,key)

def _positive(value):
    try:
        d=Decimal(str(value))
        return d if d.is_finite() and d>0 else None
    except (InvalidOperation,TypeError,ValueError):
        return None

def asset_info_evidence(info):
    if not isinstance(info,dict):
        return {}
    out={}
    if info.get("market"): out["market"]=market(info["market"])
    if info.get("currency"): out["currency"]=str(info["currency"]).upper()
    if info.get("term"): out["settlement"]=settlement(info["term"])
    lot=_positive(info.get("units_per_lot"))
    if lot is not None:
        out["provider_units_per_lot"]=str(lot)
    out["provider_type"]=str(info.get("type") or "")
    return out

def fixed_income_evidence(info, analytics, simulation, *, integer_quantity_proven=False):
    """Normalize only explicit IOL semantics plus cross-source integer proof."""
    out=asset_info_evidence(info)
    sim=simulation if isinstance(simulation,dict) else {}
    an=analytics if isinstance(analytics,dict) else {}
    if "clean_price_per100" in sim or "dirty_price_per100" in sim:
        out["price_quote_unit"]=100
    if integer_quantity_proven and _positive(sim.get("nominals"))==Decimal("1"):
        out["quantity_min"]=1
        out["quantity_step"]=1
    maturity=sim.get("maturity_date") or (an.get("calculation_inputs") or {}).get("maturity_date")
    if maturity: out["maturity_date"]=maturity
    if sim.get("payment_currency"): out["payment_currency"]=str(sim["payment_currency"]).upper()
    if sim.get("cash_flows"):
        out["coupon_terms"]="IOL_EXPLICIT_CASHFLOW_SCHEDULE"
        out["amortization_terms"]="IOL_EXPLICIT_CASHFLOW_SCHEDULE"
    out["iol_nominal_simulation"]="READ_ONLY"
    return out

_OPTION_DESCRIPTION=re.compile(
    r"^(?:Call|Put)\s+([A-Z0-9.]+)\s+\$.*?Vencimiento:\s*(\d{2}/\d{2}/\d{4})$",
    re.IGNORECASE,
)

def option_underlying_hint(info):
    text=str((info or {}).get("description") or "").strip()
    m=_OPTION_DESCRIPTION.match(text)
    return m.group(1).upper() if m else None

def option_evidence(info, chain_row, *, underlying):
    out=asset_info_evidence(info)
    row=chain_row if isinstance(chain_row,dict) else {}
    if str(row.get("symbol") or "").upper()!=str((info or {}).get("symbol") or "").upper():
        return out
    out["underlying"]=str(underlying).upper()
    right=str(row.get("option_type") or "").upper()
    if right in {"C","CALL"}: out["put_call"]="CALL"
    elif right in {"V","P","PUT"}: out["put_call"]="PUT"
    if _positive(row.get("strike_price")) is not None: out["strike"]=row["strike_price"]
    if row.get("expiration"): out["expiry_at"]=row["expiration"]
    lot=_positive((info or {}).get("units_per_lot"))
    if lot is not None: out["quantity_step"]=str(lot)
    # Deliberately no contract_multiplier: units_per_lot is an order-lot field,
    # not proof of the option's economic multiplier.
    return out

def caucion_evidence(row, *, currency="ARS"):
    if not isinstance(row,dict):
        return {}
    out={"currency":str(currency).upper()}
    if int(row.get("days") or 0)>0: out["term_days"]=int(row["days"])
    if row.get("due_date"): out["due_date"]=row["due_date"]
    if _positive(row.get("min_amount")) is not None:
        out["minimum_principal"]=row["min_amount"]
        out["principal_step"]=1
    if row.get("rate") not in (None,""):
        out["annual_rate"]=row["rate"]
    return out

def fci_evidence(info, fund, quote=None):
    out=asset_info_evidence(info)
    row=fund if isinstance(fund,dict) else {}
    if row.get("operable") is not None: out["operable"]=bool(row.get("operable"))
    if row.get("currency"): out["currency"]=str(row["currency"]).upper()
    if row.get("market"): out["market"]=market(row["market"])
    # A market quote is retained as unit_price evidence; it is NOT relabeled NAV
    # unless a provider explicitly publishes NAV semantics.
    q=quote if isinstance(quote,dict) else {}
    if q.get("unit_price") not in (None,""): out["unit_price"]=q["unit_price"]
    ts=((q.get("trade") or {}).get("timestamp") if isinstance(q.get("trade"),dict) else None)
    if ts: out["unit_price_observed_at"]=ts
    return out

def ppi_integer_quantity_proven(records):
    """PPI XHR decimal precision 0 + IOL 1-nominal simulation can prove step=1."""
    for record in records or ():
        if str(record.get("source_class") or "").upper()!="PPI_AUTHENTICATED_XHR":
            continue
        ev=record.get("evidence") or {}
        if ev.get("quantity_decimal_places") in (0,"0"):
            return True
    return False
