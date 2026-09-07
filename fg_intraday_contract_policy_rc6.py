"""RC6 intraday contract-state correction policy.

Pure/offline policy extracted before wiring into cf_intraday_scalping.py.
Two bugs are addressed:
1) a still-forming minute may change; its stored baseline must be refreshed so
   that the same legitimate revision is not misclassified after the cutoff;
2) mutable-point evidence is session-scoped and must not poison later trading
   days forever.

A revision to an actually closed point remains a hard rejection.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ=ZoneInfo('America/Argentina/Buenos_Aires')
DEFAULT_MUTABLE_SECONDS=120

class IntradayContractPolicyError(ValueError): pass

def aware(value):
    try: dt=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except ValueError as exc: raise IntradayContractPolicyError('TIMESTAMP_INVALID') from exc
    if dt.tzinfo is None or dt.utcoffset() is None: raise IntradayContractPolicyError('TIMESTAMP_NAIVE')
    return dt

def local_day(value): return aware(value).astimezone(TZ).date()

def same_trading_day(previous_checked_at, received_at)->bool:
    if not previous_checked_at: return False
    return local_day(previous_checked_at)==local_day(received_at)

def previous_for_session(previous:dict|None, *, received_at)->dict|None:
    """Discard prior-day state; never carry rejection counters across sessions."""
    if not previous: return None
    if not same_trading_day(previous.get('checked_at'),received_at): return None
    return previous

def classify_revision(*, event_at, received_at, old_price, old_volume, new_price, new_volume, mutable_seconds=DEFAULT_MUTABLE_SECONDS)->dict:
    """Classify same-event revision without inventing provider semantics.

    SAME: identical evidence.
    REFRESH_MUTABLE: changed point still inside mutable window; refresh baseline.
    REJECT_CLOSED_REVISION: changed point older than window; hard fail-closed.
    """
    received=aware(received_at).astimezone(timezone.utc); event=aware(event_at).astimezone(timezone.utc)
    if event>received+timedelta(seconds=5): raise IntradayContractPolicyError('EVENT_IN_FUTURE')
    same=(str(old_price)==str(new_price) and str(old_volume)==str(new_volume))
    age=(received-event).total_seconds()
    if same: return {'action':'SAME','age_seconds':age,'refresh':False,'closed_revision':False}
    if age < 0: raise IntradayContractPolicyError('EVENT_IN_FUTURE')
    if age <= int(mutable_seconds):
        return {'action':'REFRESH_MUTABLE','age_seconds':age,'refresh':True,'closed_revision':False}
    return {'action':'REJECT_CLOSED_REVISION','age_seconds':age,'refresh':False,'closed_revision':True}

def session_counters(previous:dict|None, *, received_at)->dict:
    p=previous_for_session(previous,received_at=received_at) or {}
    return {
        'observations':int(p.get('observations',0)),
        'stable_overlap':int(p.get('stable_overlap',0)),
        'changed_closed_points':int(p.get('changed_closed_points',0)),
        'previous_state':p.get('state'),
    }

def assert_fail_closed():
    assert DEFAULT_MUTABLE_SECONDS==120
