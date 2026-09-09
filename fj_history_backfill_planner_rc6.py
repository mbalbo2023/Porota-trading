"""RC6 historical backfill planner (offline/shadow only)."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
class BackfillPlanError(ValueError): pass
@dataclass(frozen=True)
class Identity:
    symbol:str; instrument_type:str; market:str; settlement:str; price_basis:str='RAW'
    def normalized(self): return tuple(str(x or '').strip().upper() for x in (self.symbol,self.instrument_type,self.market,self.settlement,self.price_basis))
    def validate(self):
        if any(x in {'','UNKNOWN'} for x in self.normalized()): raise BackfillPlanError('IDENTITY_UNVERIFIED')
        if self.normalized()[-1] not in {'RAW','ADJUSTED'}: raise BackfillPlanError('PRICE_BASIS_UNVERIFIED')
@dataclass(frozen=True)
class SourcePolicy:
    ppi_enabled:bool=True; iol_enabled:bool=True; a3_enabled:bool=False; a3_identity_aligned:bool=False; data912_enabled:bool=False; max_attempts_per_source:int=2
@dataclass(frozen=True)
class AttemptState: day:str; source:str; attempts:int=0; last_error:str=''
def _day(v):
    try: return date.fromisoformat(str(v)[:10])
    except ValueError as exc: raise BackfillPlanError('DATE_INVALID') from exc
def eligible_sources(policy:SourcePolicy):
    out=[]
    if policy.ppi_enabled: out.append('PPI')
    if policy.iol_enabled: out.append('IOL')
    if policy.a3_enabled and policy.a3_identity_aligned: out.append('A3_BYMA')
    if policy.data912_enabled: out.append('DATA912')
    return out
def plan(*,identity:Identity,expected_dates,canonical_dates,attempt_states=(),policy=SourcePolicy(),as_of_date=None):
    identity.validate(); expected=sorted({_day(x) for x in expected_dates}); observed={_day(x) for x in canonical_dates}
    if not expected: return {'status':'NO_EXPECTED_DATES','jobs':[],'gaps':[],'canonical_write':'DENY'}
    if as_of_date is not None:
        cutoff=_day(as_of_date); expected=[d for d in expected if d < cutoff]
    gaps=[d for d in expected if d not in observed]; state={(_day(x.day),str(x.source).upper()):x for x in attempt_states}; sources=eligible_sources(policy); jobs=[]
    for d in gaps:
        chosen=None; blocked=[]
        for source in sources:
            a=state.get((d,source),AttemptState(d.isoformat(),source,0,''))
            if a.attempts < policy.max_attempts_per_source: chosen=(source,a.attempts+1); break
            blocked.append({'source':source,'attempts':a.attempts,'last_error':a.last_error})
        if chosen: jobs.append({'identity':identity.normalized(),'date':d.isoformat(),'source':chosen[0],'attempt_number':chosen[1],'canonical_write':'DENY','reason':'MISSING_CANONICAL_DAY'})
        else: jobs.append({'identity':identity.normalized(),'date':d.isoformat(),'source':None,'attempt_number':None,'canonical_write':'DENY','reason':'SOURCES_EXHAUSTED','blocked_sources':blocked})
    return {'status':'GAPS_FOUND' if gaps else 'COMPLETE','jobs':jobs,'gaps':[d.isoformat() for d in gaps],'canonical_write':'DENY','source_policy':sources}
def assert_shadow_only():
    assert SourcePolicy().data912_enabled is False and SourcePolicy().a3_enabled is False
