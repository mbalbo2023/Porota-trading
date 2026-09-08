"""Pure RC6 scraping/Contract Evidence semaphore.

Consumes already-collected coverage + timestamps. No browser, network, broker,
DB writes or order imports. Completeness requires independent expected-universe
proof; freshness is evaluated separately against canonical SLA thresholds.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from rc6_dom_coverage_reconciler import CoverageResult, readiness
from rc6_data_sla_policy import DataSLA

@dataclass(frozen=True)
class ScrapingSemaphore:
    family:str
    color:str
    coverage_state:str
    freshness_state:str
    complete_proven:bool
    age_seconds:int|None
    reason:str

def _utc(value):
    if isinstance(value, datetime):
        dt=value
    else:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ValueError('SCRAPING_TIMESTAMP_NAIVE')
    return dt.astimezone(timezone.utc)

def freshness(*,last_success_at,now,sla:DataSLA):
    if not last_success_at:
        return 'NO_SUCCESS',None
    age=max(0,int((_utc(now)-_utc(last_success_at)).total_seconds()))
    if age>sla.max_staleness_seconds:
        return 'STALE',age
    if age>sla.warning_after_seconds:
        return 'WARNING',age
    return 'FRESH',age

def evaluate(*,coverage:CoverageResult,last_success_at,now,sla:DataSLA):
    cov=readiness(coverage)
    fresh,age=freshness(last_success_at=last_success_at,now=now,sla=sla)

    # Red is reserved for unusable/stale data, not merely unproven completeness.
    if fresh in {'NO_SUCCESS','STALE'}:
        color='RED'
        reason='FRESHNESS_UNUSABLE'
    elif cov=='COVERAGE_GREEN' and fresh=='FRESH':
        color='GREEN'
        reason='COVERAGE_AND_FRESHNESS_PROVEN'
    elif cov=='COVERAGE_GRAY':
        color='GRAY'
        reason='EXPECTED_UNIVERSE_NOT_PROVEN'
    else:
        color='YELLOW'
        reason='COVERAGE_OR_FRESHNESS_PARTIAL'

    return ScrapingSemaphore(
        family=coverage.family,color=color,coverage_state=cov,
        freshness_state=fresh,complete_proven=coverage.complete_proven,
        age_seconds=age,reason=reason)
