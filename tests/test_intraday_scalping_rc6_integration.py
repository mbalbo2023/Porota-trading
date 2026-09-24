from datetime import datetime, timedelta
import json

from be_paper_engine import PaperStore
import bu_instrument_catalog as catalog
import cf_intraday_scalping as scalping

START=datetime.fromisoformat('2026-09-07T10:30:00-03:00')

def record():
    return dict(ticker='GGAL',instrument_type='ACCIONES',market='BYMA',currency='ARS',
                settlement='A-24HS',capability='READY_PAPER_SPOT',status='AVAILABLE')

def payload(count, *, changed_index=None, changed_price=False):
    rows=[]
    for i in range(count):
        p=100+i/10
        v=20 if i%2==0 else 10
        if changed_index==i:
            if changed_price: p+=0.05
            v+=1
        rows.append({'date':(START+timedelta(minutes=i)).isoformat(),'price':p,'volume':v})
    return rows

def make_store(tmp_path):
    s=PaperStore(str(tmp_path/'paper.db')); catalog.init_schema(s); scalping.init_schema(s); return s


def test_recent_mutable_revision_refreshes_baseline_then_ages_cleanly(tmp_path):
    s=make_store(tmp_path); r=record()
    # Point at 10:39 is only 60s old: a provider finalization is mutable.
    first=scalping.normalize_payload(payload(10),received_at='2026-09-07T10:40:00-03:00')
    scalping.persist_payload(s,r,first,received_at='2026-09-07T10:40:00-03:00')
    revised=scalping.normalize_payload(payload(10,changed_index=9,changed_price=True),received_at='2026-09-07T10:40:30-03:00')
    second=scalping.persist_payload(s,r,revised,received_at='2026-09-07T10:40:30-03:00')
    assert second['changed']==0 and second['refreshed']==1
    # The same finalized point later becomes closed and must match the refreshed baseline.
    aged=scalping.normalize_payload(payload(11,changed_index=9,changed_price=True),received_at='2026-09-07T10:42:30-03:00')
    third=scalping.persist_payload(s,r,aged,received_at='2026-09-07T10:42:30-03:00')
    assert third['changed']==0
    with s.connect() as c:
        state=dict(c.execute('SELECT * FROM ppi_intraday_contract_state').fetchone())
        point=c.execute("SELECT price,volume FROM ppi_intraday_points WHERE event_at LIKE '2026-09-07T13:39:%'").fetchone()
    assert state['changed_closed_points']==0
    assert point is not None and point[0]=='100.95' and point[1]=='11'


def test_genuine_closed_revision_still_hard_rejects(tmp_path):
    s=make_store(tmp_path); r=record()
    first=scalping.normalize_payload(payload(10),received_at='2026-09-07T10:40:00-03:00')
    scalping.persist_payload(s,r,first,received_at='2026-09-07T10:40:00-03:00')
    revised=scalping.normalize_payload(payload(11,changed_index=0,changed_price=True),received_at='2026-09-07T10:41:00-03:00')
    out=scalping.persist_payload(s,r,revised,received_at='2026-09-07T10:41:00-03:00')
    assert out['state']=='REJECTED_MUTABLE_CLOSED_POINTS' and out['changed']==1


def test_prior_day_rejection_does_not_poison_new_session(tmp_path):
    s=make_store(tmp_path); r=record(); ident=scalping._identity(r)
    with s.connect() as c:
        c.execute("INSERT OR REPLACE INTO ppi_intraday_contract_state VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (*ident,'REJECTED_MUTABLE_CLOSED_POINTS',500,300,9000,0,None,
                   '2026-09-04T19:59:00.000000+00:00','old'))
    today=scalping.normalize_payload(payload(10),received_at='2026-09-07T10:40:00-03:00')
    out=scalping.persist_payload(s,r,today,received_at='2026-09-07T10:40:00-03:00')
    with s.connect() as c: state=dict(c.execute('SELECT * FROM ppi_intraday_contract_state').fetchone())
    assert state['observations']==1 and state['changed_closed_points']==0
    assert out['state']=='PENDING_LIVE_CONFIRMATION'


def test_partial_shadow_capability_is_observable_only():
    assert "READY_SHADOW_PARTIAL" in scalping.SCALPING_PAPER_CAPABILITIES
