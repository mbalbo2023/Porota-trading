"""RC6 Event Risk SHADOW data contract.

This module is deliberately non-operational: no BUY/SELL, no broker, no DB and
no network. It defines event provenance and the anti-look-ahead rule required
for future event-driven backtests.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

EVENT_TYPES=frozenset({'WAR_ESCALATION','CEASEFIRE','CEASEFIRE_BREAKDOWN','SANCTIONS','OIL_SUPPLY_SHOCK','SHIPPING_DISRUPTION','ENERGY_INFRA_ATTACK','CENTRAL_BANK','FX_INTERVENTION','REGULATORY','CORPORATE_FILING','CORPORATE_ACTION','DEFAULT_RESTRUCTURING','NATURAL_DISASTER','CYBER_INCIDENT','MARKET_HALT','POLITICAL_SHOCK'})
SOURCE_TIERS=frozenset({'TIER_A_OFFICIAL','TIER_B_MULTI_SOURCE_CONFIRMED','TIER_C_SINGLE_SOURCE','TIER_D_RUMOR_UNVERIFIED'})

class EventRiskError(ValueError): pass

def _ts(v):
    try: x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    except ValueError as exc: raise EventRiskError('TIMESTAMP_INVALID') from exc
    if x.tzinfo is None: raise EventRiskError('TIMESTAMP_NAIVE')
    return x.astimezone(timezone.utc)

@dataclass(frozen=True)
class EventEvidence:
    event_id:str
    event_type:str
    first_seen_at:str
    published_at:str
    available_to_engine_at:str
    source:str
    source_tier:str
    provenance_url:str
    payload_hash:str
    region:str='GLOBAL'
    confirmed_at:str|None=None
    retracted_at:str|None=None
    entities:tuple[str,...]=()
    exposures:tuple[str,...]=()
    title:str=''
    source_domain:str=''

    def validate(self):
        if not self.event_id or self.event_type not in EVENT_TYPES: raise EventRiskError('EVENT_ID_OR_TYPE_INVALID')
        if self.source_tier not in SOURCE_TIERS or not self.source or not self.payload_hash: raise EventRiskError('PROVENANCE_INCOMPLETE')
        first,pub,available=_ts(self.first_seen_at),_ts(self.published_at),_ts(self.available_to_engine_at)
        if available < first: raise EventRiskError('AVAILABLE_BEFORE_FIRST_SEEN')
        if self.confirmed_at and _ts(self.confirmed_at) < first: raise EventRiskError('CONFIRMATION_BEFORE_FIRST_SEEN')
        if self.retracted_at and _ts(self.retracted_at) < first: raise EventRiskError('RETRACTION_BEFORE_FIRST_SEEN')
        return True

def evidence_available(events, *, as_of):
    cutoff=_ts(as_of); result=[]
    for e in events:
        e.validate()
        if _ts(e.available_to_engine_at) <= cutoff: result.append(e)
    return sorted(result,key=lambda e:_ts(e.available_to_engine_at))

def backtest_view(event:EventEvidence, *, as_of)->dict:
    event.validate(); cutoff=_ts(as_of)
    if _ts(event.available_to_engine_at)>cutoff: return {'status':'NOT_YET_KNOWN','event':None}
    confirmed=bool(event.confirmed_at and _ts(event.confirmed_at)<=cutoff)
    retracted=bool(event.retracted_at and _ts(event.retracted_at)<=cutoff)
    return {'status':'AVAILABLE','event_id':event.event_id,'event_type':event.event_type,'source_tier':event.source_tier,'confirmed':confirmed,'retracted':retracted,'region':event.region,'entities':event.entities,'exposures':event.exposures,'available_to_engine_at':event.available_to_engine_at,'operational_action':'SHADOW_ONLY'}

def assert_shadow_only():
    assert 'BUY' not in EVENT_TYPES and 'SELL' not in EVENT_TYPES
