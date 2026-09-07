import ast,inspect
import fg_intraday_contract_policy_rc6 as m


def test_revision_at_one_minute_refreshes_baseline_not_rejects():
    r=m.classify_revision(event_at='2026-09-07T14:49:00-03:00',received_at='2026-09-07T14:50:00-03:00',old_price='100',old_volume='10',new_price='101',new_volume='12')
    assert r['action']=='REFRESH_MUTABLE' and r['refresh'] and not r['closed_revision']

def test_same_recent_point_is_stable():
    r=m.classify_revision(event_at='2026-09-07T14:49:00-03:00',received_at='2026-09-07T14:50:30-03:00',old_price='100',old_volume='10',new_price='100',new_volume='10')
    assert r['action']=='SAME'

def test_revision_after_cutoff_is_hard_rejection():
    r=m.classify_revision(event_at='2026-09-07T14:45:00-03:00',received_at='2026-09-07T14:50:00-03:00',old_price='100',old_volume='10',new_price='101',new_volume='10')
    assert r['action']=='REJECT_CLOSED_REVISION' and r['closed_revision']

def test_previous_day_rejection_does_not_poison_new_session():
    previous={'checked_at':'2026-09-04T16:59:00-03:00','observations':500,'stable_overlap':300,'changed_closed_points':9000,'state':'REJECTED_MUTABLE_CLOSED_POINTS'}
    assert m.previous_for_session(previous,received_at='2026-09-07T10:31:00-03:00') is None
    c=m.session_counters(previous,received_at='2026-09-07T10:31:00-03:00')
    assert c=={'observations':0,'stable_overlap':0,'changed_closed_points':0,'previous_state':None}

def test_same_day_rejection_is_preserved_fail_closed():
    previous={'checked_at':'2026-09-07T14:30:00-03:00','observations':20,'stable_overlap':8,'changed_closed_points':1,'state':'REJECTED_MUTABLE_CLOSED_POINTS'}
    c=m.session_counters(previous,received_at='2026-09-07T14:31:00-03:00')
    assert c['changed_closed_points']==1 and c['previous_state']=='REJECTED_MUTABLE_CLOSED_POINTS'

def test_no_network_db_order_capability():
    m.assert_fail_closed(); tree=ast.parse(inspect.getsource(m)); imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
    assert not any(x in {'sqlite3','requests','httpx'} or 'order' in x.lower() or 'ppi' in x.lower() for x in imports)
