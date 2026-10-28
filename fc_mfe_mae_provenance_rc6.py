"""RC6 provenance-aware MFE/MAE measurement (offline only).

No prices are synthesized. A measurement is valid only when every observation
belongs to the exact financial identity, falls inside the trade lifetime and
carries an explicit executable-price provenance. Missing evidence is NO_MEDIDO.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

EXECUTABLE_KINDS=frozenset({"BID","ASK","REPLAY_EXECUTABLE"})

class ExcursionError(ValueError): pass

def _d(v,name):
    try: x=Decimal(str(v))
    except (InvalidOperation,TypeError,ValueError) as exc: raise ExcursionError(name+'_INVALID') from exc
    if not x.is_finite(): raise ExcursionError(name+'_NONFINITE')
    return x

def _ts(v):
    try: x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    except ValueError as exc: raise ExcursionError('TIMESTAMP_INVALID') from exc
    if x.tzinfo is None: raise ExcursionError('TIMESTAMP_NAIVE')
    return x.astimezone(timezone.utc)

@dataclass(frozen=True)
class Identity:
    symbol:str; instrument_type:str; market:str; settlement:str; currency:str
    def normalized(self):
        return tuple(str(x or '').strip().upper() for x in (self.symbol,self.instrument_type,self.market,self.settlement,self.currency))
    def validate(self):
        x=self.normalized()
        if any(v in {'','UNKNOWN'} for v in x): raise ExcursionError('IDENTITY_UNVERIFIED')

@dataclass(frozen=True)
class TradeWindow:
    trade_id:str; identity:Identity; side:str; entry_price:object; opened_at:str; closed_at:str

@dataclass(frozen=True)
class PriceObservation:
    identity:Identity; observed_at:str; price:object; kind:str; source:str; source_event_at:str|None=None; payload_hash:str=''


def measure(trade:TradeWindow, observations:Iterable[PriceObservation])->dict:
    trade.identity.validate(); side=str(trade.side).upper()
    if side not in {'LONG','SHORT'}: raise ExcursionError('SIDE_UNSUPPORTED')
    entry=_d(trade.entry_price,'ENTRY_PRICE')
    if entry<=0: raise ExcursionError('ENTRY_PRICE_NONPOSITIVE')
    start,end=_ts(trade.opened_at),_ts(trade.closed_at)
    if end<start: raise ExcursionError('TRADE_WINDOW_INVALID')
    usable=[]; rejected=0
    for o in observations:
        try:
            o.identity.validate()
            if o.identity.normalized()!=trade.identity.normalized():
                rejected+=1; continue
            at=_ts(o.observed_at)
            if at<start or at>end: rejected+=1; continue
            kind=str(o.kind or '').upper()
            # LONG exits must use BID/replay; SHORT exits must use ASK/replay.
            allowed={'BID','REPLAY_EXECUTABLE'} if side=='LONG' else {'ASK','REPLAY_EXECUTABLE'}
            if kind not in allowed: rejected+=1; continue
            p=_d(o.price,'OBSERVED_PRICE')
            if p<=0: rejected+=1; continue
            if not str(o.source or '').strip(): rejected+=1; continue
            usable.append((at,p,o))
        except ExcursionError:
            rejected+=1
    if not usable:
        return {'status':'NO_MEDIDO','trade_id':trade.trade_id,'reason':'NO_EXECUTABLE_PROVENANCE','observations_used':0,'observations_rejected':rejected}
    usable.sort(key=lambda x:x[0])
    returns=[((p-entry)/entry if side=='LONG' else (entry-p)/entry,at,p,o) for at,p,o in usable]
    best=max(returns,key=lambda x:x[0]); worst=min(returns,key=lambda x:x[0])
    return {
        'status':'MEDIDO','trade_id':trade.trade_id,'side':side,
        'mfe_exec_return':best[0],'mae_exec_return':worst[0],
        'mfe_price':best[2],'mae_price':worst[2],
        'mfe_at':best[1].isoformat(),'mae_at':worst[1].isoformat(),
        'entry_price':entry,'observations_used':len(usable),'observations_rejected':rejected,
        'source_set':sorted({str(x[3].source) for x in returns}),
        'provenance':[
            {'observed_at':at.isoformat(),'source':str(o.source),'kind':str(o.kind).upper(),'source_event_at':o.source_event_at,'payload_hash':o.payload_hash}
            for at,_,o in usable
        ],
    }


def assert_offline_only():
    assert EXECUTABLE_KINDS==frozenset({'BID','ASK','REPLAY_EXECUTABLE'})
