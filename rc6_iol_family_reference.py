"""Bounded family-specific IOL reference collector for RC6.

Read-only only.  Complements PPI metadata without execution/account tools.
Persists evidence with provenance; missing terms remain missing.
"""
from __future__ import annotations
import json, os, sqlite3, math, re
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from rc6_multisource_discovery import canonical_family, canonical_market, canonical_settlement

SCHEMA="rc6-iol-family-reference-v1"
DEFAULT_ROOT=Path(os.getenv("POROTA_IOL_SHADOW_ROOT","/opt/porota-trading/data/market"))
DEFAULT_DB=os.getenv("POROTA_IOL_OPERATIONAL_DB","/opt/porota-trading/data/paper_v17/observer_v17.db")
MAX_OPTION_UNDERLYINGS=4
MAX_OPTION_SERIES_INFO=1


def _brief_call(client, name, arguments):
    """One bounded retry for transient read failures; never an execution call."""
    try:
        return client.call(name, arguments)
    except Exception:
        return client.call(name, arguments)


def _response_rows(payload):
    """Normalize MCP/tool envelopes without interpreting absence as zero."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("result", "structuredContent", "items", "rates", "funds", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _response_rows(value)
            if nested:
                return nested
    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    nested = _response_rows(json.loads(item["text"]))
                except (ValueError, TypeError):
                    nested = []
                if nested:
                    return nested
    return []


def _lkg_section_state(prior, section, has_value, now):
    if not has_value:
        return "SOURCE_UNAVAILABLE_NO_LKG"
    seen = (prior.get("section_observed_at") or {}).get(section)
    try:
        age = (now-datetime.fromisoformat(str(seen).replace("Z","+00:00"))).total_seconds()
    except (TypeError, ValueError):
        age = None
    return "LKG_FRESH_SOURCE_UNAVAILABLE" if age is not None and 0 <= age <= 86400 else "LKG_STALE_SOURCE_UNAVAILABLE"

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
        columns={str(r[1]) for r in c.execute("PRAGMA table_info(financial_instrument_catalog)")}
        description="description" if "description" in columns else "'' AS description"
        rows=[dict(r) for r in c.execute(f"""SELECT ticker,instrument_type,market,currency,settlement,status,{description}
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


def _fixed_flow_reference(analytics: dict, simulation: dict, units_per_lot: object,
                          *, received_at: str) -> dict:
    """Reconcile two IOL calculation units; never certify an issuer cashflow.

    Analytics reports flows for the quoted lot, while the simulation requests
    exactly one nominal.  A provider calculation clock is not a trade/book
    clock and two responses from the same provider are not independent terms.
    """
    base = {"status": "NO_VERIFICADO", "entry_authority": False,
            "contractual_authority": "ISSUER_TERMS_NOT_VERIFIED",
            "market_quote_freshness": "NO_VERIFICADO"}

    def reject(reason):
        return {**base, "reason": reason}

    def decimal(value):
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("NONFINITE")
        return result

    def day(value):
        if not isinstance(value, str):
            raise ValueError("DATE_MISSING")
        try:
            return date.fromisoformat(value)
        except ValueError:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()

    try:
        lot = decimal(units_per_lot)
        if lot != lot.to_integral_value() or not 1 <= lot <= 1000000:
            return reject("LOT_UNIT_UNVERIFIED")
        if decimal(simulation.get("nominals")) != 1:
            return reject("ONE_NOMINAL_SIMULATION_REQUIRED")
        payment_currency = simulation.get("payment_currency")
        if payment_currency not in {"ARS", "USD"}:
            return reject("PAYMENT_CURRENCY_UNVERIFIED")
        calc_times = []
        capture = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        if capture.tzinfo is None:
            return reject("CAPTURE_CLOCK_UNVERIFIED")
        # The analytics endpoint can return a cached calculation while the
        # one-nominal simulator refreshes. These are schedule references, not
        # synchronous executable quotes; preserve their separate ages.
        for response in (analytics, simulation):
            clock = datetime.fromisoformat(str(response["calculation_date"]).replace("Z", "+00:00"))
            if clock.tzinfo is None or not 0 <= (capture-clock).total_seconds() <= 86400:
                return reject("CALCULATION_CLOCK_UNVERIFIED")
            calc_times.append(clock)
        if abs((calc_times[0]-calc_times[1]).total_seconds()) > 86400:
            return reject("CALCULATION_COHORT_MISMATCH")
        maturity = day((analytics.get("calculation_inputs") or {}).get("maturity_date"))
        if day(simulation.get("maturity_date")) != maturity:
            return reject("MATURITY_MISMATCH")
        settlement = day(simulation.get("settlement_date"))
        analytic_rows, simulated_rows = analytics.get("cashflow"), simulation.get("cash_flows")
        if (not isinstance(analytic_rows, list) or not isinstance(simulated_rows, list)
                or not 0 < len(analytic_rows) == len(simulated_rows) <= 250):
            return reject("FLOW_ROWS_UNAVAILABLE_OR_MISMATCHED")
        prior = settlement
        previous_balance = None
        previous_period = None
        flows = []
        for a, s in zip(analytic_rows, simulated_rows):
            if not isinstance(a, dict) or not isinstance(s, dict):
                return reject("FLOW_ROW_INVALID")
            payable = day(a.get("date"))
            if (payable != day(s.get("date")) or not prior < payable <= maturity
                    or a.get("period_number") != s.get("period_number")):
                return reject("FLOW_DATE_OR_PERIOD_MISMATCH")
            period = s.get("period_number")
            if (type(period) is not int or period < 1 or
                    (previous_period is not None and period != previous_period + 1)):
                return reject("FLOW_PERIOD_SEQUENCE_INVALID")
            for field in ("opening_balance", "interest", "amortization", "cash_flow", "residual_balance"):
                left, right = decimal(a.get(field)), decimal(s.get(field))
                if left < 0 or right < 0 or abs(left/lot-right) > Decimal("0.000001"):
                    return reject("FLOW_UNIT_MISMATCH")
            opening = decimal(s["opening_balance"])
            interest = decimal(s["interest"])
            amortization = decimal(s["amortization"])
            amount = decimal(s["cash_flow"])
            residual = decimal(s["residual_balance"])
            if (opening <= 0 or amortization > opening or
                    abs(opening-amortization-residual) > Decimal("0.000001") or
                    (previous_balance is not None and
                     abs(previous_balance-opening) > Decimal("0.000001"))):
                return reject("FLOW_BALANCE_CONSERVATION_INVALID")
            if abs(interest+amortization-amount) > Decimal("0.000001"):
                return reject("FLOW_COMPONENT_MISMATCH")
            flows.append({"payment_date": payable.isoformat(), "amount": str(amount),
                          "currency": payment_currency})
            prior = payable
            previous_balance = residual
            previous_period = period
        if prior != maturity or decimal(simulated_rows[-1]["residual_balance"]) != 0:
            return reject("TERMINAL_FLOW_UNVERIFIED")
        total = decimal(simulation.get("total_to_collect"))
        if abs(sum((Decimal(flow["amount"]) for flow in flows), Decimal(0))-total) > Decimal("0.000001"):
            return reject("FLOW_TOTAL_MISMATCH")
    except (ValueError, TypeError, KeyError, OverflowError, InvalidOperation):
        return reject("FLOW_EVIDENCE_INCOMPLETE")
    return {**base, "status": "RECONCILED_PROVIDER_CALCULATION", "reason": "",
            "payment_currency": payment_currency, "nominal_unit": "CASH_PER_NOMINAL",
            # TTL starts with the older calculation, never the convenient
            # newer simulation that could extend a stale analytics schedule.
            "source_calculated_at": min(calc_times).isoformat(),
            "analytics_calculated_at": calc_times[0].isoformat(),
            "simulation_calculated_at": calc_times[1].isoformat(),
            "calculation_gap_seconds": int(abs((calc_times[0]-calc_times[1]).total_seconds())),
            "received_at": capture.isoformat(), "flows": flows,
            "provider_independence": "SAME_IOL_PROVIDER_ONLY"}


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
    asset_currency = str(asset.get("currency") or "").upper()
    if asset_currency != currency:
        return None
    if asset.get("symbol") and str(asset["symbol"]).upper() != symbol.upper():
        return None
    if canonical_settlement(asset.get("term"), expected_family) != settlement:
        return None
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
    if not all(math.isfinite(x) for x in (dirty, unit_price, lot_price, simulated_unit)):
        return None
    multiplier = 1.0 / units_per_lot
    maturity=(analytics.get("calculation_inputs") or {}).get("maturity_date") or simulation.get("maturity_date")
    return {
      "family":expected_family,"currency":currency,
      "market":canonical_market(asset.get("market") or "BYMA"),"settlement":settlement,
      "cash_multiplier":format(multiplier, ".12g"),"quantity_step":"1",
      "minimum_quantity":"1",
      "paper_quantity_min":"1","paper_quantity_step":"1",
      "broker_minimum_quantity":"NO_VERIFICADO","broker_quantity_step":"NO_VERIFICADO",
      "paper_quantity_policy":"ONE_NOMINAL_SIMULATION_UNIT",
      "metadata_source":"IOL_ASSET_INFO+IOL_QUOTE_PRICE_BASES+IOL_FIXED_INCOME_SIMULATION_1_NOMINAL",
      "fixed_income_evidence":{
        "quote_basis":"EXPLICIT_IOL_LOT_PRICE_WITH_UNIT_PRICE_CROSSCHECK",
        "simulated_nominals":simulation.get("nominals"),
        "quote_basis_nominal":str(units_per_lot),
        "paper_quantity_step_nominal":"1",
        "paper_minimum_nominal":"1",
        "dirty_price_per100":simulation.get("dirty_price_per100"),
        "amount_invested":simulation.get("amount_invested"),
        "amount_invested_ars":simulation.get("amount_invested_ars"),
        "iol_unit_price":unit_price,"iol_lot_price":lot_price,
        "maturity_date":maturity,
      }
    }


def _option_expiry(value):
    """Normalize IOL's local BYMA expiry timestamp to an aware ISO instant.

    IOL currently returns option expiries without an offset (for example
    2026-10-16T15:30:00).  BYMA publishes the expiration-day trading cutoff
    in Argentina local time, so a naive provider value is explicitly bound to
    America/Argentina/Buenos_Aires rather than silently treated as UTC.
    """
    if not value:
        return None
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError):
        return None
    if parsed.tzinfo is None:
        parsed=parsed.replace(tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    return parsed.isoformat()


def _option_records(chain:dict, infos:dict[str,dict], observed_at:str, *,
                    underlying_info:dict|None=None):
    """Preserve option-chain observations without certifying a series contract.

    The chain has no currency or adjustment state per series. Asset info can
    identify an exact series, but does not establish that its strike and lot
    survived corporate actions unchanged. A contract needs independent,
    effective-dated series adjustment evidence; this IOL path has none.
    """
    underlying=str(chain.get("underlying") or "").upper()
    underlying_info=underlying_info if isinstance(underlying_info,dict) else {}
    underlying_family=canonical_family(underlying_info.get("type") or underlying_info.get("asset_type"))
    underlying_exact=str(underlying_info.get("symbol") or "").strip().upper()==underlying
    contract_lot=100 if underlying_exact and underlying_family=="ACCIONES" else None
    rows=[]
    options=[r for r in chain.get("options",[]) if isinstance(r,dict)]
    options.sort(key=lambda r:(bool(r.get("is_stale")), -(float(r.get("volume") or 0))))
    for raw in options:
        symbol=str(raw.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        info=infos.get(symbol,{}) if isinstance(infos,dict) else {}
        info=info if isinstance(info,dict) else {}
        exact_info=(str(info.get("symbol") or "").strip().upper()==symbol
                    and bool(symbol) and canonical_family(info.get("type"))=="OPCIONES")
        expiry=_option_expiry(raw.get("expiration"))
        market=canonical_market(info.get("market")) if exact_info and info.get("market") else None
        currency=str(info.get("currency") or "").strip().upper() if exact_info else None
        if currency not in {"ARS","USD","USD_MEP","USD_CCL"}:
            currency=None
        # BYMA option premium settles T+0 under the current clearing contract.
        settlement="INMEDIATA"
        rows.append({
          "ticker":symbol,"instrument_type":"OPCIONES","market":market,
          "currency":currency,"settlement":settlement,
          "description":str(info.get("description") or "") if exact_info else "",
          "source":"IOL_COMPLEMENTARY","observed_at":observed_at,
          "quote":{"bid":raw.get("bid_price"),"ask":raw.get("ask_price"),
                   "volume":raw.get("volume"),"is_stale":raw.get("is_stale")},
          "financial_contract_v17":None,
          "option_chain_evidence":{
              **{k:raw.get(k) for k in ("option_id","option_type","strike_price","expiration",
                                        "implied_volatility","delta","gamma","theta","vega","rho")},
              "underlying":underlying,
              "expires_at":expiry,
              "underlying_family":underlying_family,
              # The standard exchange lot is a reference, not an assertion
              # about this series after an extraordinary adjustment.
              "standard_lot_reference":contract_lot,
              "standard_lot_reference_source":"BYMA_OPTION_LOT_POLICY_2026" if contract_lot else None,
              "series_identity_state":"EXACT_IOL_ASSET_INFO" if exact_info and market=="BYMA" and currency else "NO_VERIFICADO",
              "series_adjustment_state":"NO_VERIFICADO",
              "contract_state":"NO_VERIFICADO",
          },
        })
    return rows


def _sanitize_prior_option(row:dict):
    """Never carry a formerly inferred option identity/contract through LKG."""
    safe=dict(row)
    prior_evidence=safe.get("option_chain_evidence")
    evidence=dict(prior_evidence) if isinstance(prior_evidence,dict) else {}
    if safe.get("financial_contract_v17") is not None:
        evidence["legacy_financial_contract_unverified"]=safe["financial_contract_v17"]
    if safe.get("market") is not None:
        evidence["legacy_market_unverified"]=safe["market"]
    if safe.get("currency") is not None:
        evidence["legacy_currency_unverified"]=safe["currency"]
    if "contract_lot" in evidence:
        evidence["legacy_contract_lot_unverified"]=evidence.pop("contract_lot")
    if "contract_lot_source" in evidence:
        evidence["legacy_contract_lot_source_unverified"]=evidence.pop("contract_lot_source")
    evidence.update(series_identity_state="NO_VERIFICADO",
                    series_adjustment_state="NO_VERIFICADO",
                    contract_state="NO_VERIFICADO")
    safe["option_chain_evidence"]=evidence
    safe.update(market=None,currency=None,financial_contract_v17=None)
    quote=safe.get("quote")
    if isinstance(quote,dict):
        safe["quote"]={**quote,"is_stale":True}
    return safe


def collect(client, *, root:Path|str|None=None, db_path:str|None=None, now=None):
    root=Path(root) if root is not None else DEFAULT_ROOT
    db=db_path or DEFAULT_DB
    at=(now or datetime.now(timezone.utc)).isoformat()
    path=root/"iol_family_reference_latest.json"
    prior=_load(path)
    state=dict(prior.get("rotation") or {})
    section_states={}
    section_observed_at=dict(prior.get("section_observed_at") or {})
    catalog=_catalog(db)
    fixed_rows=[r for r in catalog
                if canonical_family(r.get("instrument_type")) in {"BONOS","LETRAS","OBLIGACIONES"}]
    fixed_by_identity={"|".join(str(r.get(k) or "").upper() for k in ("ticker","instrument_type","market","currency","settlement")):r for r in fixed_rows if r.get("ticker")}
    prior_record_tickers={str(r.get("ticker") or "").upper()
                          for r in prior.get("records",[]) if isinstance(r,dict)}
    seeded_key=next((key for preferred in ("AL30","YMCJO")
                     for key,row in fixed_by_identity.items()
                     if str(row.get("ticker") or "").upper()==preferred
                     and preferred not in prior_record_tickers),None)
    fixed_key=seeded_key or _rotate(list(fixed_by_identity),state,"fixed_index")
    fixed_symbol=str(fixed_by_identity[fixed_key]["ticker"]).upper() if fixed_key else None

    # Option chains are keyed by their underlying, not by the option ticker.
    # Rotate provider-confirmed underlyings across every BYMA family for which
    # BYMA currently lists standardized options.
    shares={str(r["ticker"]).upper() for r in catalog
            if canonical_family(r.get("instrument_type"))=="ACCIONES"
            and canonical_market(r.get("market"))=="BYMA"}
    described=[]
    for row in catalog:
        if canonical_family(row.get("instrument_type")) != "OPCIONES":
            continue
        match=re.search(r"\b(?:CALL|PUT)\s+([A-Z0-9.]+)\b",str(row.get("description") or "").upper())
        if match and match.group(1) in shares:
            described.append(match.group(1))
    underlyings=sorted(set(described)) or sorted(shares)
    if "GGAL" in underlyings:
        underlyings=["GGAL"]+[item for item in underlyings if item!="GGAL"]
    has_option_cache=any(str(r.get("instrument_type") or "").upper()=="OPCIONES"
                         for r in prior.get("records",[]) if isinstance(r,dict))
    start=(int(state.get("option_underlying_index",0)) if has_option_cache else 0) % max(1,len(underlyings))
    option_underlyings=(underlyings[start:]+underlyings[:start])[:MAX_OPTION_UNDERLYINGS]
    state["option_underlying_index"]=(start+len(option_underlyings))%max(1,len(underlyings))

    records=[(_sanitize_prior_option(r) if canonical_family(r.get("instrument_type"))=="OPCIONES" else r)
             for r in prior.get("records",[]) if isinstance(r,dict)]
    by_key={(r.get("instrument_type"),r.get("ticker"),r.get("market"),r.get("currency"),r.get("settlement")):r for r in records}
    errors=[]

    if fixed_symbol:
        identity=fixed_by_identity[fixed_key]
        try:
            family=canonical_family(identity.get("instrument_type"))
            currency=str(identity.get("currency") or "").upper()
            settlement=canonical_settlement(identity.get("settlement"),family)
            asset=_brief_call(client,"get_asset_info",{"symbol":fixed_symbol,"market":"BCBA"})
            analytics=_brief_call(client,"get_fixed_income_analytics",{"ticker":fixed_symbol})
            simulation=_brief_call(client,"simulate_fixed_income_by_nominals",{
                "ticker":fixed_symbol,"nominals":1,"currency":currency})
            quote=_brief_call(client,"get_asset_quote",{
                "symbol":fixed_symbol,"market":"BCBA",
                "term":"t0" if settlement=="INMEDIATA" else "t1"})
            contract=_fixed_contract(
                fixed_symbol,asset,analytics,simulation,quote,
                identity_family=family,identity_currency=currency,
                identity_settlement=settlement)
            if not contract:
                raise ValueError("FIXED_CONTRACT_INCOMPLETE")
            flow_reference=_fixed_flow_reference(
                analytics, simulation, asset.get("units_per_lot"),
                received_at=(now or datetime.now(timezone.utc)).isoformat())
            flow_analytics={
                "provider_flow_status":flow_reference["status"],
                "provider_flow_reason":flow_reference["reason"],
                "provider_flow_authority":"IOL_SAME_PROVIDER_REFERENCE_ONLY",
            }
            if flow_reference["status"]=="RECONCILED_PROVIDER_CALCULATION":
                flow_analytics["flows"]={
                    "value":flow_reference["flows"],
                    "source":"IOL_FIXED_INCOME_ANALYTICS+ONE_NOMINAL_SIMULATION",
                    "source_at":flow_reference["source_calculated_at"],
                    "analytics_calculated_at":flow_reference["analytics_calculated_at"],
                    "simulation_calculated_at":flow_reference["simulation_calculated_at"],
                    "calculation_gap_seconds":flow_reference["calculation_gap_seconds"],
                    "received_at":flow_reference["received_at"],
                    "clock_basis":"FEATURE_COMPUTED_AT",
                    "unit":flow_reference["nominal_unit"],
                    "payment_currency":flow_reference["payment_currency"],
                    "provider_independence":flow_reference["provider_independence"],
                    "contractual_authority":flow_reference["contractual_authority"],
                    "entry_authority":False,
                }
            row={"ticker":fixed_symbol,"instrument_type":family,
                 "market":canonical_market(identity.get("market") or asset.get("market") or "BYMA"),
                 "currency":currency,"settlement":settlement,
                 "description":str(asset.get("description") or ""),
                 "source":"IOL_COMPLEMENTARY","observed_at":at,
                 "units_per_lot":asset.get("units_per_lot"),
                 "financial_contract_v17":contract,
                 "fixed_income_analytics":{
                   "maturity_date":(analytics.get("calculation_inputs") or {}).get("maturity_date")
                       or simulation.get("maturity_date"),
                   "dirty_price":(analytics.get("prices") or {}).get("dirty_price"),
                   "technical_value":(analytics.get("prices") or {}).get("technical_value"),
                   "simulation_payment_currency":simulation.get("payment_currency"),
                   **flow_analytics,
                 }}
            by_key[(family,fixed_symbol,row["market"],currency,settlement)]=row
            successful_sections=["fixed_income"]
            section_states["fixed_income"]="LIVE_FRESH"
            section_observed_at["fixed_income"]=at
        except Exception as exc:
            errors.append("FIXED:"+fixed_symbol+":"+type(exc).__name__)
            prior_fixed = any(canonical_family(r.get("instrument_type")) in
                              {"BONOS","LETRAS","OBLIGACIONES"}
                              for r in prior.get("records",[]) if isinstance(r,dict))
            section_states["fixed_income"]=_lkg_section_state(
                prior,"fixed_income",prior_fixed,now or datetime.now(timezone.utc))
            successful_sections=[]
    else:
        successful_sections=[]

    for underlying in option_underlyings:
        try:
            chain=_brief_call(client,"get_options_chain",{"symbol":underlying})
            chain_underlying=str(chain.get("underlying") or "").strip().upper()
            if chain_underlying != str(underlying).strip().upper():
                raise ValueError("IOL_OPTION_CHAIN_UNDERLYING_MISMATCH")
            underlying_info=_brief_call(client,"get_asset_info",{"symbol":underlying,"market":"BCBA"})
            if str(underlying_info.get("symbol") or "").strip().upper() != chain_underlying:
                raise ValueError("IOL_OPTION_UNDERLYING_INFO_MISMATCH")
            candidates=[r for r in chain.get("options",[]) if isinstance(r,dict) and r.get("symbol")]
            candidates.sort(key=lambda r:(bool(r.get("is_stale")), -(float(r.get("volume") or 0))))
            infos={}
            for candidate in candidates:
                symbol=str(candidate["symbol"]).strip().upper()
                if not symbol:
                    continue
                if symbol in infos:
                    continue
                if len(infos)>=MAX_OPTION_SERIES_INFO:
                    break
                try:
                    # One identity read per underlying, without retry. A
                    # failed optional lookup must not amplify provider load.
                    infos[symbol]=client.call("get_asset_info",{
                        "symbol":symbol,"market":"BCBA"})
                except Exception:
                    infos[symbol]={}
                    errors.append("OPTIONS:"+underlying+":SERIES_INFO_UNAVAILABLE:"+symbol)
            option_rows=_option_records(chain,infos,at,underlying_info=underlying_info)
            if not option_rows:
                errors.append("OPTIONS:"+underlying+":EMPTY_UNEXPECTED")
                section_states["options:"+underlying]="EMPTY_UNEXPECTED"
                continue
            # A fresh chain supersedes old rows for this underlying, including
            # legacy records keyed with currency inherited from the share.
            by_key={key:row for key,row in by_key.items()
                    if not (canonical_family(row.get("instrument_type"))=="OPCIONES"
                            and str((row.get("option_chain_evidence") or {}).get("underlying") or "").upper()==underlying)}
            for row in option_rows:
                by_key[("OPCIONES",row["ticker"],row["market"],row["currency"],row["settlement"])]=row
            successful_sections.append("options:"+underlying)
            section_states["options:"+underlying]="LIVE_FRESH"
            section_observed_at["options:"+underlying]=at
        except Exception as exc:
            errors.append("OPTIONS:"+underlying+":"+type(exc).__name__)
            section="options:"+underlying
            prior_options=any(str(r.get("instrument_type") or "").upper()=="OPCIONES"
                              and str((r.get("option_chain_evidence") or {}).get("underlying") or "").upper()==underlying
                              for r in prior.get("records",[]) if isinstance(r,dict))
            section_states[section]=_lkg_section_state(
                prior,section,prior_options,now or datetime.now(timezone.utc))

    # Broad family discovery/reference calls. They do not authorize execution.
    fci=prior.get("fci",[])
    cauciones=prior.get("cauciones",{})
    try:
        fresh=_response_rows(_brief_call(client,"get_fci_funds",{}))
        if fresh:
            fci=fresh; successful_sections.append("fci")
            section_states["fci"]="LIVE_FRESH";section_observed_at["fci"]=at
        else:
            errors.append("FCI:EMPTY_UNEXPECTED");section_states["fci"]="EMPTY_UNEXPECTED"
    except Exception as exc:
        errors.append("FCI:"+type(exc).__name__);section_states["fci"]=_lkg_section_state(
            prior,"fci",bool(fci),now or datetime.now(timezone.utc))
    for currency in ("ARS","USD"):
        try:
            fresh=_response_rows(_brief_call(client,"get_caucion_rates",{"currency":currency,"caucion_type":"colocadora"}))
            key="caucion:"+currency
            if fresh:
                cauciones[currency]=fresh; successful_sections.append(key)
                section_states[key]="LIVE_FRESH";section_observed_at[key]=at
            else:
                errors.append("CAUCION_"+currency+":EMPTY_UNEXPECTED");section_states[key]="EMPTY_UNEXPECTED"
        except Exception as exc:
            errors.append("CAUCION_"+currency+":"+type(exc).__name__)
            section="caucion:"+currency
            section_states[section]=_lkg_section_state(
                prior,section,bool(cauciones.get(currency)),now or datetime.now(timezone.utc))

    prior_cauciones=prior.get("cauciones") if isinstance(prior.get("cauciones"),dict) else {}
    prior_usable=(bool(prior.get("records")) or bool(prior.get("fci")) or
                  any(bool(rows) for rows in prior_cauciones.values()))
    # A refresh timestamp alone is not LKG evidence.  Old v1 payloads could be
    # freshly rewritten while every IOL section was unavailable.
    prior_good=prior.get("last_known_good_at") if prior_usable else None
    try:
        prior_age=((now or datetime.now(timezone.utc))-datetime.fromisoformat(
            str(prior_good).replace("Z","+00:00"))).total_seconds()
    except (TypeError,ValueError):
        prior_age=None
    cache_state=("LIVE_FRESH" if successful_sections else
                 "CACHE_FRESH" if prior and prior_age is not None and 0<=prior_age<=86400 else
                 "CACHE_STALE" if prior_usable and prior_good else "SOURCE_UNAVAILABLE")
    payload={"schema":SCHEMA,"refreshed_at":at,"source":"IOL_MCP","decision_effect":"OBSERVE_ONLY",
             "real_money_authorized":False,"rotation":state,"records":list(by_key.values()),
             "fci":fci if isinstance(fci,list) else [],"cauciones":cauciones if isinstance(cauciones,dict) else {},
             "cache_state":cache_state,"live_sections":successful_sections,
             "section_states":section_states,"section_observed_at":section_observed_at,
             "fallback_order":["IOL_LIVE_BOUNDED_RETRY","IOL_LAST_KNOWN_GOOD",
                               "PPI_PRIMARY","BYMA_PUBLIC_COMPLEMENTARY"],
             "continuation_state":"CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
             "last_known_good_at":at if successful_sections else prior.get("last_known_good_at"),
             "errors":errors}
    _atomic(path,payload)
    return payload
