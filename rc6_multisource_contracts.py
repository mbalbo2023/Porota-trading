"""RC6 field-level multi-source contract consolidation.

PPI, IOL and BYMA/A3 are complementary evidence providers. A field may be
completed by another provider when the primary source omits it. Comparable
non-empty values that disagree are a hard conflict. This module is pure:
no network, no broker account and no order capability.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation

SOURCE_RANK = {
    "PPI_STRUCTURED_API": 10,
    "PPI_AUTHENTICATED_XHR": 20,
    "PPI_AUTHENTICATED_WEB": 30,
    "IOL_MCP": 20,
    "BYMA_MARKET_DATA_API": 10,
    "BYMA_PUBLIC": 30,
    "A3_PRIMARY_API": 10,
    "A3_RISK_POSTTRADE": 10,
}
ALIASES = {
    "ON":"OBLIGACIONES", "OBLIGACIONES_NEGOCIABLES":"OBLIGACIONES",
    "ETF":"ETFS", "CEDEAR":"CEDEARS",
}
FIELD_ALIASES = {
    "market": ("market", "mercado"),
    "currency": ("currency", "moneda"),
    "settlement": ("settlement", "term", "plazo"),
    "instrument_id": ("instrument_id", "itemId", "id_instrumento"),
    "price_quote_unit": ("price_quote_unit", "price_unit_nominals"),
    "quantity_min": ("quantity_min", "minimum_quantity", "min_quantity"),
    "quantity_step": ("quantity_step", "order_quantity_step"),
    "maturity_date": ("maturity_date", "maturity", "fecha_vencimiento"),
    "payment_currency": ("payment_currency",),
    "underlying": ("underlying", "subyacente"),
    "put_call": ("put_call", "option_right", "right"),
    "strike": ("strike", "strike_price"),
    "expiry_at": ("expiry_at", "expiry", "expiration"),
    "lot_size": ("lot_size", "contract_lot"),
    "contract_multiplier": ("contract_multiplier", "multiplier"),
    "initial_margin": ("initial_margin", "margin_requirement"),
    "maintenance_margin": ("maintenance_margin",),
    "term_days": ("term_days", "days"),
    "annual_rate": ("annual_rate", "rate", "tna"),
    "minimum_principal": ("minimum_principal", "min_amount"),
    "principal_step": ("principal_step",),
    "due_date": ("due_date", "maturity_at"),
    "nav": ("nav", "nav_value", "unit_price"),
    "nav_date": ("nav_date", "nav_as_of"),
    "cutoff": ("cutoff", "cutoff_time"),
    "redemption_term": ("redemption_term",),
    "subscription_minimum": ("subscription_minimum", "subscription_min"),
    "subscription_step": ("subscription_step",),
    "operable": ("operable",),
    "fee_schedule": ("fee_schedule", "cost_model"),
}

def family(value):
    key=str(value or "").strip().upper().replace("-","_").replace(" ","_")
    return ALIASES.get(key,key)

def _value(payload, names):
    if not isinstance(payload, dict):
        return None
    lowered={str(k).lower():v for k,v in payload.items()}
    for name in names:
        v=lowered.get(name.lower())
        if v not in (None,"",[],{}):
            return v
    return None

def canonical_fields(payload):
    result={}
    for field,names in FIELD_ALIASES.items():
        value=_value(payload,names)
        if value in (None,"",[],{}):
            continue
        if field!="fee_schedule" and isinstance(value,(dict,list,tuple,set)):
            continue
        result[field]=value
    return result

def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError):
        return None

def _same(field, left, right):
    if field in {"strike","price_quote_unit","quantity_min","quantity_step","lot_size",
                 "contract_multiplier","initial_margin","maintenance_margin","term_days",
                 "annual_rate","minimum_principal","principal_step","nav","subscription_minimum",
                 "subscription_step"}:
        a,b=_decimal(left),_decimal(right)
        return a is not None and b is not None and a==b
    return str(left).strip().upper()==str(right).strip().upper()

def merge(records):
    """Merge canonical fields and retain exact provenance. Conflicts fail closed."""
    candidates={}
    for record in records or ():
        if not isinstance(record,dict):
            continue
        source=str(record.get("source_class") or record.get("source") or "").upper()
        if source not in SOURCE_RANK:
            continue
        payload=record.get("evidence") if isinstance(record.get("evidence"),dict) else record
        for field,value in canonical_fields(payload).items():
            candidates.setdefault(field,[]).append({
                "rank":SOURCE_RANK[source],"source":source,"value":value,
                "observed_at":record.get("observed_at"),"source_ref":record.get("source_ref"),
            })
    merged={}; provenance={}; conflicts={}
    for field,items in candidates.items():
        ordered=sorted(items,key=lambda x:x["rank"])
        distinct=[]
        for item in ordered:
            if not any(_same(field,item["value"],prior["value"]) for prior in distinct):
                distinct.append(item)
        if len(distinct)>1:
            conflicts[field]=distinct
            continue
        winner=ordered[0]
        merged[field]=winner["value"]
        provenance[field]={k:winner.get(k) for k in ("source","observed_at","source_ref")}
    return {"fields":merged,"provenance":provenance,"conflicts":conflicts}

SIMULATOR_IMPLEMENTED = frozenset({
    "ACCIONES","CEDEARS","ETFS","BONOS","LETRAS","OBLIGACIONES","CAUCIONES",
})

PAPER_REQUIRED = {
    "ACCIONES": {"market","currency","settlement","quantity_step"},
    "CEDEARS": {"market","currency","settlement","quantity_step"},
    "ETFS": {"market","currency","settlement","quantity_step"},
    "BONOS": {"market","currency","settlement","price_quote_unit","quantity_step","maturity_date"},
    "LETRAS": {"market","currency","settlement","price_quote_unit","quantity_step","maturity_date"},
    "OBLIGACIONES": {"market","currency","settlement","price_quote_unit","quantity_step","maturity_date"},
    "OPCIONES": {"market","currency","settlement","underlying","put_call","strike","expiry_at",
                 "quantity_step","contract_multiplier"},
    "FUTUROS": {"market","currency","settlement","underlying","expiry_at","quantity_step",
                "contract_multiplier","initial_margin"},
    "CAUCIONES": {"currency","term_days","annual_rate","minimum_principal","principal_step","due_date"},
    "FCI": {"currency","nav","nav_date","subscription_minimum","subscription_step","cutoff","redemption_term"},
}

def readiness(family_name, records):
    fam=family(family_name)
    combined=merge(records)
    if combined["conflicts"]:
        return {**combined,"family":fam,"status":"BLOCKED_CONFLICT",
                "paper_simulatable":False,"real_money_authorized":False}
    required=PAPER_REQUIRED.get(fam)
    if required is None:
        return {**combined,"family":fam,"status":"BLOCKED_UNSUPPORTED_FAMILY",
                "missing":["family_contract"],"paper_simulatable":False,"real_money_authorized":False}
    missing=sorted(required-set(combined["fields"]))
    complete=not missing
    simulator_ready=fam in SIMULATOR_IMPLEMENTED
    status=("PENDING_CONTRACT_EVIDENCE" if missing else
            "READY_PAPER_CONTRACT" if simulator_ready else
            "CONTRACT_READY_SIMULATOR_PENDING")
    return {**combined,"family":fam,"status":status,"missing":missing,
            "contract_complete":complete,"simulator_implemented":simulator_ready,
            "paper_simulatable":complete and simulator_ready,
            "real_money_authorized":False}

def fixed_income_instrument_contract(symbol, family_name, record):
    """Build PAPER math only after the consolidated fixed-income contract is complete."""
    fam=family(family_name)
    state=readiness(fam,record if isinstance(record,(list,tuple)) else [record])
    if state["status"]!="READY_PAPER_CONTRACT" or fam not in {"BONOS","LETRAS","OBLIGACIONES"}:
        return None
    f=state["fields"]
    basis=_decimal(f["price_quote_unit"])
    step=_decimal(f["quantity_step"])
    if basis is None or basis<=0 or step is None or step<=0:
        return None
    return {
        "symbol":str(symbol).upper(),"family":fam,"currency":str(f["currency"]).upper(),
        "market":str(f["market"]).upper(),"settlement":str(f["settlement"]).upper(),
        "cash_multiplier":str(Decimal(1)/basis),"quantity_step":str(step),
        "metadata_source":"MULTISOURCE_CONTRACT_V1",
        "expires_at":None,"initial_margin":None,"maintenance_margin":None,
        "underlying":None,"strike":None,"option_right":None,
    }
