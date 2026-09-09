"""RC6 caucion contract primitives backed by explicit PPI evidence.

No order methods. Unknown/uncertified economics remain explicit blockers.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo

_TICKER_RE=re.compile(r"^(PESOS|DOLAR)([1-9][0-9]*)$")
_MINIMUM={"PESOS":Decimal("100000"),"DOLAR":Decimal("100")}
TZ=ZoneInfo("America/Argentina/Buenos_Aires")
OPEN=time(10,30); CLOSE=time(17,0)

class CaucionContractError(ValueError): pass

@dataclass(frozen=True)
class CaucionIdentity:
    ticker:str
    currency_prefix:str
    term_days:int


def parse_ticker(ticker:str)->CaucionIdentity:
    raw=str(ticker or '').strip().upper(); m=_TICKER_RE.fullmatch(raw)
    if not m: raise CaucionContractError(f"INVALID_CAUCION_TICKER:{raw}")
    prefix,days=m.groups(); return CaucionIdentity(raw,prefix,int(days))


def minimum_principal(ticker:str)->Decimal:
    return _MINIMUM[parse_ticker(ticker).currency_prefix]


def validate_amount(ticker:str,amount)->Decimal:
    identity=parse_ticker(ticker)
    try: value=Decimal(str(amount))
    except (InvalidOperation,TypeError,ValueError) as exc: raise CaucionContractError('AMOUNT_NOT_NUMERIC') from exc
    if not value.is_finite() or value!=value.to_integral_value(): raise CaucionContractError('AMOUNT_MUST_BE_INTEGER_STEP_1')
    if value<_MINIMUM[identity.currency_prefix]: raise CaucionContractError('AMOUNT_BELOW_MINIMUM')
    return value


def gross_interest(ticker:str,capital,tna_percent)->Decimal:
    identity=parse_ticker(ticker); principal=validate_amount(ticker,capital)
    rate=Decimal(str(tna_percent))
    if rate<0: raise CaucionContractError('NEGATIVE_TNA')
    return principal*(rate/Decimal('100'))*Decimal(identity.term_days)/Decimal('365')


def known_ppi_commission_percent(ticker:str)->Decimal|None:
    """Exact published agent commission where evidence is exact.

    PPI ARS colocadora: 2% + IVA annual. USD publication says 'hasta 1%', so
    exact USD commission intentionally remains unknown until reconciled.
    """
    return Decimal('2') if parse_ticker(ticker).currency_prefix=='PESOS' else None


def inside_session(moment:datetime)->bool:
    if moment.tzinfo is None: raise CaucionContractError('TIMEZONE_REQUIRED')
    local=moment.astimezone(TZ).timetz().replace(tzinfo=None)
    return OPEN<=local<CLOSE


def evidence_blocker(ticker:str)->str:
    identity=parse_ticker(ticker)
    if identity.currency_prefix=='DOLAR':
        return 'USD_EXACT_COMMISSION_AND_MARKET_RIGHTS_UNVERIFIED'
    return 'MARKET_RIGHTS_UNVERIFIED'

ORDER_ROUTING_ALLOWED=False
