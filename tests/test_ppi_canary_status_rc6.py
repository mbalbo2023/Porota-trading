import importlib.util, pathlib, sqlite3, tempfile

ROOT=pathlib.Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

can=load('can','ops/ppi_web_canary_selector_rc6.py')
st=load('st','ops/ppi_progress_status_rc6.py')

def test_canary_is_deterministic_and_one_per_family():
    rows=[
      {'symbol':'AAA','instrument_type':'ACCIONES','market':'BYMA','settlement':'A-48HS','residual_class':'PARTIAL_VALID'},
      {'symbol':'BBB','instrument_type':'ACCIONES','market':'BYMA','settlement':'A-48HS','residual_class':'PROVIDER_INVALID'},
      {'symbol':'CCC','instrument_type':'FUTUROS','market':'ROFEX','settlement':'INMEDIATA','residual_class':'NO_PROVIDER_ROWS'},
      {'symbol':'DDD','instrument_type':'OPCIONES','market':'BYMA','settlement':'A-48HS','residual_class':'HARD_PROVIDER_ERROR'},
    ]
    a=can.select_canary(rows,max_total=8,max_per_family=1)
    b=can.select_canary(list(reversed(rows)),max_total=8,max_per_family=1)
    assert [can.identity(x) for x in a]==[can.identity(x) for x in b]
    assert len({x['instrument_type'] for x in a})==len(a)==3
    assert next(x for x in a if x['instrument_type']=='ACCIONES')['symbol']=='BBB'

def test_eta_never_invents_without_recent_completions():
    out=st.estimate_eta([],12)
    assert out['eta_minutes'] is None and out['confidence']=='UNAVAILABLE'

def test_api_status_counts_errors_as_terminal(tmp_path):
    db=tmp_path/'x.db'; c=sqlite3.connect(db)
    c.executescript('''
    create table ppi_history_ingest_tasks(run_id text,state text,finished_at text);
    create table ppi_history_ingest_runtime(run_id text,heartbeat text,disk_free_gib real,safety text,status text,family text,last_committed_batch integer,rows_canonical integer);
    ''')
    rid=st.API_RUN_ID
    c.executemany('insert into ppi_history_ingest_tasks values(?,?,?)',[(rid,'DONE_VALID',None),(rid,'ERROR',None),(rid,'PENDING',None)])
    from datetime import datetime,timezone
    c.execute('insert into ppi_history_ingest_runtime values(?,?,?,?,?,?,?,?)',(rid,datetime.now(timezone.utc).isoformat(),9.0,'PRODUCTION_PAPER|0','RUNNING','FUTUROS',2,100))
    c.commit(); c.close()
    x=st.api_status(db,expected=3)
    assert x['terminal']==2 and x['pending']==1 and x['progress_pct']==66.67
    assert x['semaphore']=='GREEN'
