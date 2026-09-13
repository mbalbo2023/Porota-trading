import importlib.util
import sys
from pathlib import Path

P=Path(__file__).resolve().parents[1]/'ops'/'ppi_web_residual_state_rc6.py'
spec=importlib.util.spec_from_file_location('ppi_web_residual_state_rc6',P)
m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)


def rows():
    return [
      {'symbol':'A','instrument_type':'BONOS','market':'BYMA','settlement':'A-48HS','residual_class':'NO_PROVIDER_ROWS'},
      {'symbol':'B','instrument_type':'FUTUROS','market':'ROFEX','settlement':'A-24HS','residual_class':'PROVIDER_INVALID'},
    ]


def test_seed_claim_finish_resume_and_atomic_status(tmp_path):
    s=m.ResidualState(tmp_path/'state.db',tmp_path/'status.json')
    s.seed('R1','abc',rows())
    x=s.summary('R1'); assert x['total']==2 and x['counts']=={'PENDING':2}
    t=s.claim_next('R1'); assert t and t['symbol']=='A'
    assert s.summary('R1')['counts']=={'PENDING':1,'RUNNING':1}
    # Simulate process death after claim. New owner recovers only orphan RUNNING.
    assert s.recover_orphan_running('R1')==1
    assert s.summary('R1')['counts']=={'PENDING':2}
    t=s.claim_next('R1'); s.finish('R1',t,'DONE_EMPTY',provider_rows=0,valid_rows=0)
    x=s.summary('R1'); assert x['terminal']==1 and x['progress_pct']==50.0
    assert (tmp_path/'status.json').is_file()


def test_run_id_is_bound_to_manifest(tmp_path):
    s=m.ResidualState(tmp_path/'state.db',tmp_path/'status.json')
    s.seed('R1','abc',rows())
    try:
        s.seed('R1','different',rows())
    except RuntimeError as e:
        assert 'RUN_ID_MANIFEST_MISMATCH' in str(e)
    else:
        raise AssertionError('manifest mismatch must fail closed')


def test_finish_requires_running_task(tmp_path):
    s=m.ResidualState(tmp_path/'state.db',tmp_path/'status.json')
    s.seed('R1','abc',rows())
    try:
        s.finish('R1',rows()[0],'DONE_VALID')
    except RuntimeError as e:
        assert 'TASK_NOT_RUNNING' in str(e)
    else:
        raise AssertionError('must not finish unclaimed task')
