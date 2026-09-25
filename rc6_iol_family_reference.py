"""Bounded family-specific IOL reference collector for RC6.

Read-only only.  Complements PPI metadata without execution/account tools.
Persists evidence with provenance; missing terms remain missing.
"""
from __future__ import annotations
import json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from rc6_multisource_discovery import canonical_family, canonical_market, canonical_settlement

SCHEMA="rc6-iol-family-reference-v1"
DEFAULT_ROOT=Path(os.getenv("POROTA_IOL_SHADOW_ROOT","/opt/porota-trading/data/market"))
DEFAULT_DB=os.getenv("POROTA_IOL_OPERATIONAL_DB","/opt/porota-trading/data/paper_v17/observer_v17.db")
MAX_OPTIONS_INFO=6

def _atomic(path:Path,value:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile("w",encoding="utf-8",dir=path.parent,prefix=".iol-family-",suffix=".tmp",delete=False) as h:
        json.dump(value,h,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        h.write("\n");h.flush();os.fsync(h.fileno());tmp=Path(h.name)
    os.chmod(tmp,0o644);os.replace(tmp,path)

def _load(path:Path):
    try:
        x=json.loads(path.read_text(encoding="utf-8"));return x if isinstance(x,dict) else {}
    except (OSError,ValueError,json.JSONDecodeError):return {}

def _catalog(db:str):
    try:
        c=sqlite3.connect(f"file:{db}?mode=ro",uri=True,timeout=5);c.row_factory=sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        rows=[dict(r) for r in c.execute("""SELECT ticker,instrument_type,market,currency,settlement,status
          FROM financial_instrument_catalog
          WHERE upper(COALESCE(status,'')) IN ('AVAILABLE','STALE','OBSERVED_SHADOW')
          ORDER BY instrument_type,ticker""")]
        c.close();return rows
    except sqlite3.Error:return []

def _rotate(values:list[str], state:dict, key:str):
    vals=sorted(set(values))
    if not vals:return None
    idx=int(state.get(key,0))%len(vals)
    state[key]=(idx+1)%len(vals)
    return vals[idx]

def _iol_shadow(root:Path):
    p=_load(root/"iol_shadow_latest.json")
    return {str(r.get("symbol") or "").upper():r for r in p.get("symbols",[]) if isinstance(r,dict)}

def _number(value):
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_price_bases(quote: dict):
    """Return explicit IOL unit and quoted-lot prices without conflating them."""
    q = quote if isinstance(quote, dict) else {}
    trade = q.get("trade") if isinstance(q.get("trade"), dict) else {}
    unit = _number(q.get("unit_price"))
    if unit is None:
        unit = _number(trade.get("unit_price"))
    lot = _number(q.get("lot_price"))
    if lot is None:
        lot = _number(trade.get("lot_price"))
    return unit, lot


def _fixed_contract(symbol:str, asset:dict, analytics:dict, simulation:dict, quote:dict,
                    *, identity_family:str|None=None, identity_currency:str|None=None,
                    identity_settlement:str|None=None):
    """Build a fixed-income PAPER contract only from dimensionally proven IOL data.

    IOL exposes two price bases for fixed income: unit_price (cash for one
    nominal) and lot_price (the quoted per-lot/per-100 market price).  The
    contract is accepted only when the live quote, one-nominal simulation and
    units_per_lot agree.  No ticker convention is used as contract evidence.
    """
    expected_family = canonical_family(identity_family or asset.get("type"))
    observed_family = canonical_family(asset.get("type"))
    if expected_family not in {"BONOS","LETRAS","OBLIGACIONES"}:
        return None
    if observed_family != expected_family:
        return None
    currency = str(identity_currency or asset.get("currency") or "").upper()
    settlement = canonical_settlement(identity_settlement or asset.get("term"), expected_family)
    try:
        nominal = float(simulation.get("nominals"))
        dirty = float(simulation.get("dirty_price_per100"))
        units_per_lot = int(asset.get("units_per_lot") or 0)
    except (TypeError, ValueError):
        return None
    unit_price, lot_price = _quote_price_bases(quote)
    if currency == "ARS":
        simulated_unit = _number(simulation.get("amount_invested_ars"))
    elif currency in {"USD", "USD_MEP", "USD_CCL"}:
        simulated_unit = _number(simulation.get("amount_invested"))
    else:
        simulated_unit = None
    if (nominal != 1 or dirty <= 0 or units_per_lot <= 0 or
            unit_price is None or unit_price <= 0 or
            lot_price is None or lot_price <= 0 or
            simulated_unit is None or simulated_unit <= 0):
        return None
    # Prove that IOL's two quote bases really differ by the explicit lot size,
    # and that the simulation of one nominal agrees with the unit quote.
    lot_ratio = lot_price / unit_price
    unit_ratio = simulated_unit / unit_price
    if not (abs(lot_ratio - units_per_lot) <= max(0.01, units_per_lot * 0.005)
            and 0.98 <= unit_ratio <= 1.02):
        return None
    multiplier = 1.0 / units_per_lot
    maturity=(analytics.get("calculation_inputs") or {}).get("maturity_date") or simulation.get("maturity_date")
    return {
      "family":expected_family,"currency":currency,
      "market":canonical_market(asset.get("market") or "BYMA"),"settlement":settlement,
      "cash_multiplier":format(multiplier, ".12g"),"quantity_step":str(units_per_lot),
      "metadata_source":"IOL_ASSET_INFO+IOL_QUOTE_PRICE_BASES+IOL_FIXED_INCOME_SIMULATION_1_NOMINAL",
      "fixed_income_evidence":{
        "quote_basis":"EXPLICIT_IOL_LOT_PRICE_WITH_UNIT_PRICE_CROSSCHECK",
        "simulated_nominals":simulation.get("nominals"),
        "quote_basis_nominal":str(units_per_lot),
        "quantity_step_nominal":str(units_per_lot),
        "minimum_nominal":str(units_per_lot),
        "dirty_price_per100":simulation.get("dirty_price_per100"),
        "amount_invested":simulation.get("amount_invested"),
        "amount_invested_ars":simulation.get("amount_invested_ars"),
        "iol_unit_price":unit_price,"iol_lot_price":lot_price,
        "maturity_date":maturity,
      }
    }


def _option_records(chain:dict, infos:dict[str,dict], observed_at:str):
    underlying=str(chain.get("underlying") or "").upper()
    rows=[]
    options=[r for r in chain.get("options",[]) if isinstance(r,dict)]
    options.sort(key=lambda r:(bool(r.get("is_stale")), -(float(r.get("volume") or 0))))
    for raw in options:
        symbol=str(raw.get("symbol") or "").upper()
        info=infos.get(symbol,{})
        try:
            lot=int(info.get("units_per_lot") or 0)
            strike=float(raw.get("strike_price"))
        except (TypeError,ValueError):
            lot=0;strike=0
        expiry=raw.get("expiration")
        right={"C":"CALL","V":"PUT","CALL":"CALL","PUT":"PUT"}.get(str(raw.get("option_type") or "").upper())
        contract=None
        if lot>0 and strike>0 and expiry and underlying and right:
            contract={
              "family":"OPCIONES","currency":str(info.get("currency") or "ARS").upper(),
              "market":canonical_market(info.get("market") or "BYMA"),
              "settlement":canonical_settlement(info.get("term") or "T0","OPCIONES"),
              "cash_multiplier":str(lot),"quantity_step":"1",
              "metadata_source":"IOL_OPTIONS_CHAIN+IOL_ASSET_INFO",
              "expires_at":expiry,"underlying":underlying,"strike":str(strike),"option_right":right,
            }
        rows.append({
          "ticker":symbol,"instrument_type":"OPCIONES","market":canonical_market(info.get("market") or "BYMA"),
          "currency":str(info.get("currency") or "ARS").upper(),
          "settlement":canonical_settlement(info.get("term") or "T0","OPCIONES"),
          "description":str(info.get("description") or ""),
          "source":"IOL_COMPLEMENTARY","observed_at":observed_at,
          "quote":{"bid":raw.get("bid_price"),"ask":raw.get("ask_price"),
                   "volume":raw.get("volume"),"is_stale":raw.get("is_stale")},
          "financial_contract_v17":contract,
          "option_chain_evidence":{k:raw.get(k) for k in ("option_id","option_type","strike_price","expiration","implied_volatility","delta","gamma","theta","vega","rho")},
        })
    return rows

def collect(client, *, root:Path|str|None=None, db_path:str|None=None, now=None):
    root=Path(root) if root is not None else DEFAULT_ROOT
    db=db_path or DEFAULT_DB
    at=(now or datetime.now(timezone.utc)).isoformat()
    path=root/"iol_family_reference_latest.json"
    prior=_load(path)
    state=dict(prior.get("rotation") or {})
    catalog=_catalog(db)
    shadow=_iol_shadow(root)
    fixed=[r["ticker"] for r in catalog if canonical_family(r.get("instrument_type")) in {"BONOS","LETRAS","OBLIGACIONES"}]
    underlyings=[r["ticker"] for r in catalog if canonical_family(r.get("instrument_type"))=="ACCIONES"]
    fixed_symbol=_rotate(fixed,state,"fixed_index")
    underlying=_rotate(underlyings,state,"option_underlying_index")
    records=[r for r in prior.get("records",[]) if isinstance(r,dict)]
    by_key={(r.get("instrument_type"),r.get("ticker")):r for r in records}
    errors=[]

    if fixed_symbol:
        try:
            asset=client.call("get_asset_info",{"symbol":fixed_symbol,"market":"BCBA"})
            analytics=client.call("get_fixed_income_analytics",{"ticker":fixed_symbol})
            simulation=client.call("simulate_fixed_income_by_nominals",{"ticker":fixed_symbol,"nominals":1,"currency":"ARS"})
            contract=_fixed_contract(fixed_symbol,asset,analytics,simulation,shadow.get(fixed_symbol,{}))
            fam=canonical_family(asset.get("type"))
            row={"ticker":fixed_symbol,"instrument_type":fam,"market":canonical_market(asset.get("market") or "BYMA"),
                 "currency":str(asset.get("currency") or "").upper(),
                 "settlement":canonical_settlement(asset.get("term"),fam),
                 "description":str(asset.get("description") or ""),"source":"IOL_COMPLEMENTARY",
                 "observed_at":at,"units_per_lot":asset.get("units_per_lot"),
                 "financial_contract_v17":contract,
                 "fixed_income_analytics":{"maturity_date":(analytics.get("calculation_inputs") or {}).get("maturity_date"),
                   "dirty_price":(analytics.get("prices") or {}).get("dirty_price"),
                   "technical_value":(analytics.get("prices") or {}).get("technical_value")}}
            by_key[(fam,fixed_symbol)]=row
        except Exception as exc:errors.append("FIXED:"+type(exc).__name__)

    if underlying:
        try:
            chain=client.call("get_options_chain",{"symbol":underlying})
            candidates=[r for r in chain.get("options",[]) if isinstance(r,dict) and r.get("symbol")]
            candidates.sort(key=lambda r:(bool(r.get("is_stale")), -(float(r.get("volume") or 0))))
            infos={}
            for raw in candidates[:MAX_OPTIONS_INFO]:
                symbol=str(raw.get("symbol") or "").upper()
                try:infos[symbol]=client.call("get_asset_info",{"symbol":symbol,"market":"BCBA"})
                except Exception as exc:errors.append("OPTION_INFO:"+symbol+":"+type(exc).__name__)
            for row in _option_records(chain,infos,at):
                by_key[("OPCIONES",row["ticker"])]=row
        except Exception as exc:errors.append("OPTIONS:"+type(exc).__name__)

    # Broad family discovery/reference calls. They do not authorize execution.
    fci=prior.get("fci",[])
    cauciones=prior.get("cauciones",{})
    try:fci=(client.call("get_fci_funds",{}) or {}).get("result",[])
    except Exception as exc:errors.append("FCI:"+type(exc).__name__)
    for currency in ("ARS","USD"):
        try:cauciones[currency]=(client.call("get_caucion_rates",{"currency":currency,"caucion_type":"colocadora"}) or {}).get("result",[])
        except Exception as exc:errors.append("CAUCION_"+currency+":"+type(exc).__name__)

    payload={"schema":SCHEMA,"refreshed_at":at,"source":"IOL_MCP","decision_effect":"OBSERVE_ONLY",
             "real_money_authorized":False,"rotation":state,"records":list(by_key.values()),
             "fci":fci if isinstance(fci,list) else [],"cauciones":cauciones if isinstance(cauciones,dict) else {},
             "errors":errors}
    _atomic(path,payload)
    return payload
