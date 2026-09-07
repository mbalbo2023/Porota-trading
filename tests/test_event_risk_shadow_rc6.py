import ast,inspect
import pytest
import fi_event_risk_shadow_rc6 as m


def event(**kw):
    d=dict(event_id='E1',event_type='OIL_SUPPLY_SHOCK',first_seen_at='2026-09-07T10:00:00+00:00',published_at='2026-09-07T09:58:00+00:00',available_to_engine_at='2026-09-07T10:00:05+00:00',source='OFFICIAL',source_tier='TIER_A_OFFICIAL',provenance_url='https://example.invalid/e1',payload_hash='abc',region='MIDDLE_EAST',confirmed_at='2026-09-07T10:20:00+00:00',entities=('OIL',),exposures=('ENERGY',))
    d.update(kw); return m.EventEvidence(**d)

def test_event_not_visible_before_engine_availability():
    e=event(); assert m.backtest_view(e,as_of='2026-09-07T10:00:04+00:00')['status']=='NOT_YET_KNOWN'

def test_later_confirmation_is_not_backdated():
    e=event(); r=m.backtest_view(e,as_of='2026-09-07T10:10:00+00:00'); assert r['status']=='AVAILABLE' and not r['confirmed']
    r2=m.backtest_view(e,as_of='2026-09-07T10:21:00+00:00'); assert r2['confirmed']

def test_available_filter_preserves_temporal_order():
    e1=event(event_id='E1',available_to_engine_at='2026-09-07T10:01:00+00:00')
    e2=event(event_id='E2',available_to_engine_at='2026-09-07T10:02:00+00:00',first_seen_at='2026-09-07T10:01:30+00:00')
    assert [x.event_id for x in m.evidence_available([e2,e1],as_of='2026-09-07T10:01:30+00:00')]==['E1']

def test_available_before_first_seen_is_invalid():
    with pytest.raises(m.EventRiskError,match='AVAILABLE_BEFORE_FIRST_SEEN'): event(available_to_engine_at='2026-09-07T09:59:00+00:00').validate()

def test_no_buy_sell_or_io_capability():
    m.assert_shadow_only(); tree=ast.parse(inspect.getsource(m)); imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
    assert not any(x in {'sqlite3','requests','httpx'} or 'order' in x.lower() for x in imports)
