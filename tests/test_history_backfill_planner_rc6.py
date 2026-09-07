import ast,inspect
import fj_history_backfill_planner_rc6 as m

ID=m.Identity('GGAL','ACCIONES','BYMA','A-24HS','RAW')

def test_gap_uses_ppi_first_and_skips_current_day():
    r=m.plan(identity=ID,expected_dates=['2026-09-03','2026-09-04','2026-09-07'],canonical_dates=['2026-09-03'],as_of_date='2026-09-07')
    assert r['gaps']==['2026-09-04']
    assert r['jobs'][0]['source']=='PPI' and r['jobs'][0]['canonical_write']=='DENY'

def test_exhausted_ppi_falls_to_iol_bounded():
    a=[m.AttemptState('2026-09-04','PPI',2,'OHLC_INCONSISTENT')]
    r=m.plan(identity=ID,expected_dates=['2026-09-04'],canonical_dates=[],attempt_states=a,as_of_date='2026-09-07')
    assert r['jobs'][0]['source']=='IOL' and r['jobs'][0]['attempt_number']==1

def test_a3_requires_alignment_and_is_disabled_by_default():
    p=m.SourcePolicy(ppi_enabled=False,iol_enabled=False,a3_enabled=True,a3_identity_aligned=False)
    r=m.plan(identity=ID,expected_dates=['2026-09-04'],canonical_dates=[],policy=p,as_of_date='2026-09-07')
    assert r['jobs'][0]['source'] is None and r['jobs'][0]['reason']=='SOURCES_EXHAUSTED'
    p2=m.SourcePolicy(ppi_enabled=False,iol_enabled=False,a3_enabled=True,a3_identity_aligned=True)
    assert m.plan(identity=ID,expected_dates=['2026-09-04'],canonical_dates=[],policy=p2,as_of_date='2026-09-07')['jobs'][0]['source']=='A3_BYMA'

def test_data912_is_not_eligible_by_default():
    assert 'DATA912' not in m.eligible_sources(m.SourcePolicy())

def test_complete_identity_has_no_jobs():
    r=m.plan(identity=ID,expected_dates=['2026-09-04'],canonical_dates=['2026-09-04'],as_of_date='2026-09-07')
    assert r['status']=='COMPLETE' and r['jobs']==[]

def test_no_calendar_guessing_when_expected_dates_empty():
    r=m.plan(identity=ID,expected_dates=[],canonical_dates=[]); assert r['status']=='NO_EXPECTED_DATES'

def test_offline_no_db_network_orders():
    m.assert_shadow_only(); tree=ast.parse(inspect.getsource(m)); imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
    assert not any(x in {'sqlite3','requests','httpx'} or 'order' in x.lower() for x in imports)
