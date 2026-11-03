from datetime import datetime
from zoneinfo import ZoneInfo

import eu_telegram_policy_rc6 as p

TZ=ZoneInfo('America/Argentina/Buenos_Aires')
SAT=datetime(2026,9,5,12,0,tzinfo=TZ)
MON=datetime(2026,9,7,12,0,tzinfo=TZ)


def test_critical_is_unsuppressible_even_non_business_day():
    d=p.decide(kind='PAPER_DAILY_LOSS',priority=0,at=SAT,business_day=False)
    assert d.allow is True
    assert d.critical_unsuppressible is True
    assert d.terminal_state=='DELIVER'


def test_routine_is_terminally_suppressed_on_weekend():
    d=p.decide(kind='CLOSING_SUMMARY',priority=30,at=SAT,business_day=False)
    assert d.allow is False
    assert d.terminal_state=='SUPPRESSED_WEEKEND'
    assert d.reason=='NON_BUSINESS_DAY_ROUTINE'


def test_routine_delivers_business_day():
    d=p.decide(kind='PAPER_FILLED_BUY',priority=20,at=MON,business_day=True)
    assert d.allow is True


def test_unknown_noncritical_is_conservatively_suppressed_nonbusiness():
    d=p.decide(kind='SOME_FUTURE_ROUTINE',priority=10,at=SAT,business_day=False)
    assert d.allow is False


def test_priority_zero_overrides_kind():
    d=p.decide(kind='CLOSING_SUMMARY',priority=0,at=SAT,business_day=False)
    assert d.allow is True
    assert d.reason=='CRITICAL_UNSUPPRESSIBLE'
