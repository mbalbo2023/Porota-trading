import json
import sqlite3

import fb_raw_evidence_exact_v1 as m


def _legacy(path):
    c=sqlite3.connect(path)
    c.execute('''CREATE TABLE historical_raw_archive(
      id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL, row_key TEXT NOT NULL,
      body_hash TEXT NOT NULL, recorded_at TEXT NOT NULL, quality TEXT NOT NULL,
      body_json TEXT NOT NULL, UNIQUE(origin,row_key,body_hash))''')
    inner=json.dumps([{'date':'2026-09-01','price':1.0}], ensure_ascii=False)
    for i in range(3):
        wrapper={'asset_class':'ACCIONES','date_from':'2026-01-01','date_to':'2026-09-01',
                 'metadata':{'market':'BYMA'},'payload_json':inner,'settlement':'A-24HS',
                 'symbol':'GGAL','valid_rows':1}
        body=m.canonical(wrapper)
        import hashlib
        h=hashlib.sha256(body.encode()).hexdigest()
        c.execute('INSERT INTO historical_raw_archive VALUES(NULL,?,?,?,?,?,?)',
                  ('PPI_HISTORY',f'k{i}',h,f'2026-09-09T00:0{i}:00+00:00','VALID_PAYLOAD',body))
    c.commit(); c.close()


def test_migrate_and_reconstruct_exact(tmp_path):
    db=tmp_path/'legacy.db'; _legacy(db)
    env={'PPI_HISTORY_RAW_STORAGE_MODE':'EXTERNAL_EXACT_V1',
         'PPI_HISTORY_EVIDENCE_ROOT':str(tmp_path/'evidence')}
    result=m.migrate_legacy(str(db), env, progress_every=0)
    assert result['checked']==3
    assert result['missing']==0
    assert result['mismatches']==0
    assert result['evidence_lost']==0
    metrics=m.metrics(env)
    assert metrics['attempts']==3
    assert metrics['unique_objects']==1


def test_future_write_roundtrips_without_legacy_db(tmp_path):
    env={'PPI_HISTORY_RAW_STORAGE_MODE':'EXTERNAL_EXACT_V1',
         'PPI_HISTORY_EVIDENCE_ROOT':str(tmp_path/'evidence')}
    wrapper={'symbol':'AL30','asset_class':'BONOS','settlement':'A-24HS',
             'date_from':'2026-01-01','date_to':'2026-09-01','metadata':{},
             'valid_rows':1,'payload_json':'[{"price":1.25, "date":"2026-09-01"}]'}
    r=m.archive_wrapper(row_key='rk',wrapper=wrapper,recorded_at='2026-09-09T00:00:00+00:00',
                        quality='VALID_PAYLOAD',environ=env)
    assert r['body_sha256']
    with sqlite3.connect(m.manifest_path(env)) as c:
        row=c.execute('select wrapper_json,inner_sha256 from exact_attempts_v1').fetchone()
    assert m.reconstruct(row[0],row[1],env)==m.canonical(wrapper)
