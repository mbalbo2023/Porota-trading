from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable, Mapping, Any
from zoneinfo import ZoneInfo

_TICKER_RE = re.compile(r"^(PESOS|DOLAR)([1-9][0-9]*)$")
_MIN_BY_PREFIX = {"PESOS": Decimal("100000"), "DOLAR": Decimal("100")}
_ARGENTINA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
_MARKET_OPEN = time(10, 30)
_MARKET_CLOSE = time(17, 0)

class CaucionContractError(ValueError):
    pass

@dataclass(frozen=True)
class CaucionIdentity:
    ticker: str
    currency_prefix: str
    term_days: int

@dataclass(frozen=True)
class CaucionQuote:
    ticker: str
    tna_percent: Decimal
    quantity: Decimal
    side_source: str

@dataclass(frozen=True)
class CaucionCostEstimate:
    gross_interest: Decimal
    ppi_commission: Decimal
    iva_on_ppi_commission: Decimal
    net_before_market_rights_and_other_taxes: Decimal
    market_rights_known: bool
    budget_is_authoritative_for_full_costs: bool


def parse_ticker(ticker: str) -> CaucionIdentity:
    raw=str(ticker).strip().upper(); match=_TICKER_RE.fullmatch(raw)
    if not match: raise CaucionContractError(f"invalid caucion ticker: {ticker!r}")
    prefix,days_raw=match.groups(); return CaucionIdentity(raw,prefix,int(days_raw))


def validate_amount(ticker: str, amount: Any) -> Decimal:
    identity=parse_ticker(ticker)
    try: value=Decimal(str(amount))
    except (InvalidOperation,ValueError,TypeError) as exc: raise CaucionContractError("amount is not numeric") from exc
    if not value.is_finite(): raise CaucionContractError("amount must be finite")
    if value != value.to_integral_value(): raise CaucionContractError("caucion amount must be an integer; quantity step is 1")
    minimum=_MIN_BY_PREFIX[identity.currency_prefix]
    if value < minimum: raise CaucionContractError(f"amount below PPI-supported minimum for {identity.currency_prefix}: {minimum}")
    return value


def gross_interest(capital: Any,tna_percent: Any,term_days:int)->Decimal:
    try: capital_d=Decimal(str(capital)); tna_d=Decimal(str(tna_percent))
    except (InvalidOperation,ValueError,TypeError) as exc: raise CaucionContractError("capital/TNA must be numeric") from exc
    if term_days<=0: raise CaucionContractError("term_days must be positive")
    if capital_d<0 or tna_d<0: raise CaucionContractError("capital and TNA must be non-negative")
    return capital_d*(tna_d/Decimal("100"))*Decimal(term_days)/Decimal("365")


def gross_interest_for_ticker(ticker:str,capital:Any,tna_percent:Any)->Decimal:
    identity=parse_ticker(ticker); validate_amount(ticker,capital); return gross_interest(capital,tna_percent,identity.term_days)


def theoretical_liquidity_date(order_load_date:date,ticker:str)->date:
    return order_load_date+timedelta(days=parse_ticker(ticker).term_days)


def is_same_day_concertation_window(moment:datetime)->bool:
    if moment.tzinfo is None: raise CaucionContractError("moment must be timezone-aware")
    local=moment.astimezone(_ARGENTINA_TZ).timetz().replace(tzinfo=None)
    return _MARKET_OPEN<=local<=_MARKET_CLOSE


def ppi_annual_commission_percent(ticker:str)->Decimal|None:
    return Decimal("2") if parse_ticker(ticker).currency_prefix=="PESOS" else None


def estimate_ppi_costs_before_market_rights(ticker:str,capital:Any,tna_percent:Any,*,ppi_commission_annual_percent:Any,iva_percent:Any)->CaucionCostEstimate:
    identity=parse_ticker(ticker); capital_d=validate_amount(ticker,capital)
    try: commission_pct=Decimal(str(ppi_commission_annual_percent)); iva_pct=Decimal(str(iva_percent))
    except (InvalidOperation,ValueError,TypeError) as exc: raise CaucionContractError("commission and IVA must be numeric") from exc
    if commission_pct<0 or iva_pct<0: raise CaucionContractError("commission and IVA must be non-negative")
    if identity.currency_prefix=="PESOS" and commission_pct!=Decimal("2"): raise CaucionContractError("PESOS colocadora PPI annual commission must be 2 percent")
    if identity.currency_prefix=="DOLAR" and commission_pct>Decimal("1"): raise CaucionContractError("DOLAR colocadora PPI annual commission cannot exceed 1 percent")
    gross=gross_interest(capital_d,tna_percent,identity.term_days)
    commission=gross_interest(capital_d,commission_pct,identity.term_days)
    iva=commission*iva_pct/Decimal("100")
    return CaucionCostEstimate(gross,commission,iva,gross-commission-iva,False,True)


def placed_side_book_levels(book_payload:Mapping[str,Any])->Iterable[Mapping[str,Any]]:
    for key in ("bids","Bids","BIDS"):
        levels=book_payload.get(key)
        if levels is not None:
            if not isinstance(levels,list): raise CaucionContractError("book bids must be a list")
            return levels
    raise CaucionContractError("book payload has no bids field")


def quote_from_book_level(ticker:str,level:Mapping[str,Any])->CaucionQuote:
    parse_ticker(ticker)
    try: price=Decimal(str(level["price"])); quantity=Decimal(str(level["quantity"]))
    except (KeyError,InvalidOperation,ValueError,TypeError) as exc: raise CaucionContractError("book level requires numeric price and quantity") from exc
    if price<0 or quantity<0: raise CaucionContractError("price/TNA and quantity must be non-negative")
    return CaucionQuote(ticker.upper(),price,quantity,"BIDS")

__all__=["CaucionContractError","CaucionIdentity","CaucionQuote","CaucionCostEstimate","parse_ticker","validate_amount","gross_interest","gross_interest_for_ticker","theoretical_liquidity_date","is_same_day_concertation_window","ppi_annual_commission_percent","estimate_ppi_costs_before_market_rights","placed_side_book_levels","quote_from_book_level"]
