"""Issue603 causal adversaries. Real isolated SQLite; no live transport."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from be_paper_engine import D, PaperBroker, PaperStore
import rc6_local_eod_history as local
from tests.test_rc6_convergence_core_admission import AT, quote
from tests.test_rc6_issue600_regressions import source, sample, NOW


def signal(tmp_path, *, step=60, older_regular=False):
    store = PaperStore(str(tmp_path/'paper.db'))
    if older_regular:
        for i in range(12):
            store.add_quote(quote(AT-timedelta(minutes=30-i), bid='95',ask='95.01'))
    for i in range(8):
        q=quote(AT-timedelta(seconds=(7-i)*step),bid=str(100+i),ask=str(D(100+i)+D('.01')))
        store.add_quote(q)
    return PaperBroker(store,clock_fn=lambda:AT.isoformat(),ai_mode='OFF'),q


@pytest.mark.parametrize('older_regular',[False,True])
def test_burst_in_last_actual_sma_vector_cannot_buy(tmp_path,monkeypatch,older_regular):
    monkeypatch.setenv('PAPER_HISTORICAL_CANDLE_SHADOW','OFF')
    broker,q=signal(tmp_path,step=1,older_regular=older_regular)
    action,score,reason,features=broker.decide(q)
    assert action=='HOLD'
    assert features['reason_code']=='SIGNAL_EVENT_SPAN_INSUFFICIENT'


def test_regular_minute_events_keep_baseline_buy(tmp_path,monkeypatch):
    monkeypatch.setenv('PAPER_HISTORICAL_CANDLE_SHADOW','OFF')
    broker,q=signal(tmp_path)
    action,score,reason,features=broker.decide(q)
    assert action=='BUY' and score>=broker.threshold(AT)
    temporal=features['entry_signal_inputs']['temporal_contract']
    assert temporal['observed_span_seconds']==420
    assert temporal['bar_duration_seconds'] is None


def test_single_old_event_cannot_pad_seven_burst_events(tmp_path,monkeypatch):
    monkeypatch.setenv('PAPER_HISTORICAL_CANDLE_SHADOW','OFF')
    broker,q=signal(tmp_path,step=1)
    with broker.store.connect() as c:
        oldest=(AT-timedelta(minutes=7)).isoformat()
        c.execute('UPDATE market_snapshots SET trade_at=?,observed_at=? WHERE id=1',(oldest,oldest))
    action,score,reason,features=broker.decide(q)
    assert action=='HOLD'
    assert features['reason_code']=='SIGNAL_EVENT_INTERVAL_TOO_SHORT'


def test_eod_spot_carry_blocks_but_caucion_carry_is_separate(source,tmp_path):
    from bq_exit_policy import PaperSessionPolicy
    assert PaperSessionPolicy().close_at_eod is True
    sample(source)
    with source.connect() as c:
        c.execute('CREATE TABLE paper_cauciones(status TEXT)')
        c.execute("INSERT INTO paper_cauciones VALUES('OPEN')")
    result=local.consolidate(source.path,tmp_path/'cold.db',as_of=NOW,free_bytes=local.RESERVE)
    assert result['state']=='LOCAL_EOD_READY'
    with source.connect() as c:
        c.execute("INSERT INTO paper_positions VALUES('OPEN')")
    result=local.consolidate(source.path,tmp_path/'cold.db',as_of=NOW,free_bytes=local.RESERVE)
    assert result['state']=='BLOCKED' and result['reason']=='OPEN_POSITIONS'


def test_process_termination_before_eod_commit_keeps_cursor_unpublished(source,tmp_path):
    sample(source)
    target=tmp_path/'cold.db'
    code='''import sys,signal
from rc6_local_eod_history import consolidate,RESERVE
def terminate_window():
 print('IN_UNCOMMITTED_BATCH',flush=True)
 signal.pause()
consolidate(sys.argv[1],sys.argv[2],as_of=sys.argv[3],free_bytes=RESERVE,before_commit=terminate_window)
'''
    p=subprocess.Popen([sys.executable,'-c',code,source.path,str(target),NOW.isoformat()],
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        import selectors
        with selectors.DefaultSelector() as selector:
            selector.register(p.stdout,selectors.EVENT_READ)
            assert selector.select(timeout=10),'fixture did not reach uncommitted batch'
            assert p.stdout.readline().strip()=='IN_UNCOMMITTED_BATCH'
        p.terminate();p.wait(timeout=5)
        assert p.returncode<0
    finally:
        if p.poll() is None:p.kill();p.wait(timeout=5)
        p.stdout.close();p.stderr.close()
    with sqlite3.connect(target) as c:
        assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        assert c.execute('SELECT COUNT(*) FROM local_eod_cursor_v1').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM history_local_daily_v1').fetchone()[0]==0
    assert local.consolidate(source.path,target,as_of=NOW,free_bytes=local.RESERVE)['imported']==1


def test_eod_combined_near_deadline_stages_exceed_old_service_budget_safely(source,tmp_path,monkeypatch):
    sample(source)
    clock=local.time.monotonic;origin=clock();shift=[0]
    monkeypatch.setattr(local.time,'monotonic',lambda:clock()+shift[0])
    admission=local._runtime_block;calls=[0]
    def runtime(read,at):
        result=admission(read,at);calls[0]+=1
        shift[0]+=1.7 if calls[0]==1 else 1.8
        return result
    prepare=local.init_schema
    def cold(*args,**kwargs):
        prepare(*args,**kwargs);shift[0]+=14.5
    monkeypatch.setattr(local,'_runtime_block',runtime)
    monkeypatch.setattr(local,'init_schema',cold)
    def publication():shift[0]+=14.5
    result=local.consolidate(source.path,tmp_path/'cold.db',as_of=NOW,
        free_bytes=local.RESERVE,before_commit=publication)
    assert result['state']=='LOCAL_EOD_READY' and result['imported']==1
    assert 30<local.time.monotonic()-origin<45


def test_eod_backlog_is_success_only_after_committed_batch(monkeypatch,capsys):
    monkeypatch.setattr(local,'consolidate',lambda *a,**k:{'state':'BACKLOG','cursor':500})
    assert local.main()==0
    assert json.loads(capsys.readouterr().out)['state']=='BACKLOG'


@pytest.mark.parametrize('state',['BLOCKED','DEFERRED_DISK','CORRUPT','UNKNOWN'])
def test_eod_unsafe_states_remain_nonzero(monkeypatch,state):
    monkeypatch.setattr(local,'consolidate',lambda *a,**k:{'state':state})
    assert local.main()!=0


def test_service_budget_covers_all_stage_deadlines_and_kills_in_container():
    unit=Path('systemd/porota-local-eod-history-rc6.service').read_text()
    timeout=int(next(line.split('=',1)[1] for line in unit.splitlines() if line.startswith('TimeoutStartSec=')))
    assert timeout>=2+15+2+15+10
    assert 'timeout --signal=TERM --kill-after=5s 45s' in unit
    assert 'SuccessExitStatus=2' not in unit


def test_eod_501_revisions_repeat_without_loss_or_duplicates(source,tmp_path):
    # A real backlog crossing the500 boundary, including revised source bars.
    for i in range(501):
        sample(source,price=str(100+i),known=(NOW-timedelta(minutes=10,seconds=501-i)).isoformat())
    target=tmp_path/'cold.db'
    first=local.consolidate(source.path,target,as_of=NOW,free_bytes=local.RESERVE)
    second=local.consolidate(source.path,target,as_of=NOW+timedelta(seconds=1),free_bytes=local.RESERVE)
    third=local.consolidate(source.path,target,as_of=NOW+timedelta(seconds=2),free_bytes=local.RESERVE)
    assert first['state']=='BACKLOG' and first['cursor']==500
    assert second['state']=='LOCAL_EOD_READY' and second['cursor']==501
    assert third['imported']==0
    with sqlite3.connect(target) as c:
        assert c.execute('SELECT COUNT(*) FROM local_eod_receipts_v1').fetchone()[0]==501
        assert c.execute('SELECT COUNT(*) FROM local_eod_cursor_v1').fetchone()[0]==1
        rows=c.execute('SELECT body_json FROM history_local_daily_v1 ORDER BY known_at').fetchall()
        assert len(rows)==2 and json.loads(rows[-1][0])['close']=='600'
