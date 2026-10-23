"""Issue599 verified adenda: retrospective evidence cannot attest live health."""
from datetime import timedelta
import json
import sqlite3

from bs4 import BeautifulSoup
import pytest

from rc6_counterfactual_learning import read_bounded
from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation
from rc6_shadow_runtime.worker import durable_exit_observations
from rc6_trader_dashboard import counterfactuals as cf
from rc6_trader_dashboard.projection import Projection, Store
from tests.test_rc6_issue600_regressions import committed
from tests.test_ws_dash_trader_terminal_08 import database, client, NOW, STAMP


def publication(tmp_path, count=12):
    value={'generated_at':STAMP,'source':'api_token=private-source', 'entries':[
        {'candidate_id':str(i),'symbol':'T000','profile':'FROZEN_PROFILE','currency':'ARS',
         'original_decision':'BUY','outcome':'WOULD_CONFIRM','comparable':True,
         'evidence_at':STAMP,'compared_at':STAMP,'reason':'Comparable archived decision'} for i in range(count)]}
    (tmp_path/'counterfactual_learning_rc6.json').write_text(json.dumps(value))
    return value


def test_old_publication_remains_historical_with_bounded_pagination(tmp_path):
    publication(tmp_path,121)
    result=read_bounded(tmp_path,as_of=NOW+timedelta(days=20),offset=90)
    assert result['state']=='HISTORICAL_OBSERVABLE'
    assert result['total']==100 and len(result['rows'])==10
    assert result['counts']['WOULD_CONFIRM']==100 and result['sample_truncated']
    assert result['as_of']==STAMP and 'private-source' not in result['source']
    assert all(r['entry_authority'] is False for r in result['rows'])


@pytest.mark.parametrize('change', ['future','naive','noncomparable','reversed'])
def test_pit_comparability_is_required(tmp_path,change):
    value=publication(tmp_path,1)
    if change=='future': value['generated_at']=(NOW+timedelta(seconds=1)).isoformat()
    elif change=='naive': value['generated_at']='2026-10-05T16:00:00'
    elif change=='noncomparable': value['entries'][0]['comparable']=False
    else: value['entries'][0]['evidence_at']=(NOW+timedelta(seconds=1)).isoformat()
    (tmp_path/'counterfactual_learning_rc6.json').write_text(json.dumps(value))
    result=read_bounded(tmp_path,as_of=NOW)
    if change in {'future','naive'}:
        assert result['total'] is None and result['counts']=={}
    else:
        assert result['counts']['INSUFFICIENT_EVIDENCE']==1
        assert result['counts']['WOULD_CONFIRM']==0


@pytest.mark.parametrize('bad', ['{broken','x'*(1024**2+1)])
def test_corrupt_or_oversized_fallback_cannot_resurrect_other_publications(tmp_path,bad):
    (tmp_path/'decision_evidence_latest.json').write_text(bad)
    result=read_bounded(tmp_path,as_of=NOW)
    assert result['reason']=='PUBLICATION_REJECTED' and result['total'] is None


def test_missing_or_symlink_publication_is_unknown(tmp_path):
    assert read_bounded(tmp_path,as_of=NOW)['counts']=={}
    (tmp_path/'counterfactual_learning_rc6.json').symlink_to('/etc/passwd')
    assert read_bounded(tmp_path,as_of=NOW)['reason']=='PUBLICATION_REJECTED'


def test_profile_adapter_uses_published_decisions_without_live_cut(tmp_path):
    value={'generated_at':STAMP,'decisions':[{'state':'VERIFIED','decision_key':'fact-1',
        'symbol':'T000','factual_decision':'BUY','evidence_at':STAMP,'currency':'ARS',
        'profiles':[{'name':'SHADOW','state':'EVALUATED','action':'CANDIDATE_OPEN'}]}]}
    (tmp_path/'decision_evidence_latest.json').write_text(json.dumps(value))
    result=read_bounded(tmp_path,as_of=NOW)
    assert result['total']==1 and result['rows'][0]['outcome']=='WOULD_CONFIRM'


def test_canonical_aliases_auth_pagination_and_redaction(client,tmp_path,monkeypatch):
    monkeypatch.setenv('POROTA_VALIDATION_ROOT',str(tmp_path))
    value=publication(tmp_path)
    value['entries'][0]['reason']='api_token=do-not-render-secret'
    (tmp_path/'counterfactual_learning_rc6.json').write_text(json.dumps(value))
    for path in ('/aprendizaje','/validacion','/analitica/contrafactuales'):
        response=client.get(path)
        assert response.status_code==200
        soup=BeautifulSoup(response.text,'html.parser')
        assert soup.select_one('#terminal-content')['data-view']=='analitica/contrafactuales'
        assert len(soup.select('tr[data-row]'))==10
        assert 'do-not-render-secret' not in response.text
        assert 'channel=gates' in response.text and 'channel=exits' in response.text
        assert soup.find('a',string='Mostrar 10 más')
    client.headers.pop('Authorization')
    assert client.get('/analitica/contrafactuales').status_code==401


def test_gate_sample_deduplicates_exact_positions_and_separates_currencies(database):
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE trade_gate_evaluations(id INTEGER PRIMARY KEY,evaluated_at TEXT,symbol TEXT,paper_id TEXT,detail_json TEXT)')
        at=(NOW-timedelta(minutes=11)).isoformat()
        detail=json.dumps({'economics':{'passed':False}})
        for i,pid in enumerate(('closed-0','closed-0','closed-2','open-1','missing-position')):
            c.execute('INSERT INTO trade_gate_evaluations VALUES(?,?,?,?,?)',(i,at,'T000',pid,detail))
    with Store(database,now=NOW) as store:
        page=cf.gates(Projection(store))
    ars=next(r for r in page.rows if r['currency']=='ARS')
    usd=next(r for r in page.rows if r['currency']=='USD')
    unknown=next(r for r in page.rows if r['currency']=='NO_VERIFICADO')
    assert ars['evaluated']==2 and ars['outcome_closed']==1 and ars['outcome_pending']==1
    assert ars['actual_paper_pnl']=='0.1' and usd['actual_paper_pnl']=='5'
    assert ars['counterfactual_if_bound']=='0' and ars['gains_removed']==1
    assert unknown['actual_paper_pnl'] is None and unknown['outcome_pending']==1
    assert not any(k.startswith('_') for r in page.rows for k in r)


def test_old_cut_revocation_is_enforced_only_for_live_consumers(tmp_path):
    committed(tmp_path)
    with EvidenceFiles(tmp_path) as files:
        files.write('status.json',{'status':'FAIL_CLOSED','as_of':STAMP})
    with pytest.raises(ValueError,match='GENERATION_REVOKED'):
        read_committed_generation(tmp_path)
    cut=read_committed_generation(tmp_path,require_current_status=False)
    assert cut['live_status_required'] is False
    with pytest.raises(ValueError,match='READER_MODE'):
        read_committed_generation(tmp_path,require_current_status='false')


def test_exit_lane_never_promotes_censored_results_or_unknown_currency(database):
    base={'entry':{'paper_id':'closed-0','symbol':'T000','currency':'ARS','asset_class':'ACCIONES',
                  'market':'BYMA','settlement':'INMEDIATA','opened_at':STAMP},
          'registered_at':STAMP,'forward_label':{'status':'CENSURADO'},
          'variants':[{'policy':'FIXED','result':{'state':'CLOSED','net':'100'}}]}
    lab=durable_exit_observations({'entries':[base]*101})
    assert len(lab['rows'])==100 and all(r['modeled_net'] is None for r in lab['rows'])
    lab['rows'][0].update(state='EVALUABLE',modeled_net='5',currency='NO_VERIFICADO')
    with Store(database,now=NOW) as store:
        p=Projection(store)
        p.shadow_history={'state':'HISTORICAL_COHERENT_SHADOW','report':{'as_of':STAMP,'economic_exit_lab':lab}}
        result=cf.exits(p)
    assert len(result.rows)==10 and result.rows[0]['modeled_net'] is None
    assert result.rows[0]['reason'].startswith('PATH_COVERAGE_NOT_VERIFIED')
    assert all(r['entry_authority'] is False for r in result.rows)


@pytest.mark.parametrize('elapsed,age,fresh', [(2,40,True),(2,65,False),(200,119,True),(200,121,False)])
def test_live_generation_liveness_uses_measured_cadence_and_source_cap(database,elapsed,age,fresh):
    with Store(database,now=NOW) as store:
        p=Projection(store)
        p.shadow={'state':'COMMITTED_COHERENT_SHADOW','report':{'as_of':(NOW-timedelta(seconds=age)).isoformat()},
                  'status':{'generation_timing':{'poll_seconds':30,'tick_seconds':elapsed}}}
        assert p.shadow_fresh is fresh
        assert p.shadow_liveness['budget_seconds']<=120


def test_global_fresh_heartbeat_does_not_hide_stale_identity_book(database):
    old=(NOW-timedelta(minutes=5)).isoformat()
    with sqlite3.connect(database) as c:
        c.execute('INSERT INTO market_snapshots(id,symbol,asset_class,market,currency,settlement,observed_at,book_at,trade_at,received_at) VALUES(1,?,?,?,?,?,?,?,?,?)',
                  ('T000','ACCIONES','BYMA','ARS','INMEDIATA',STAMP,old,old,STAMP))
        c.execute('INSERT INTO market_snapshots(id,symbol,asset_class,market,currency,settlement,observed_at,book_at,trade_at,received_at) VALUES(2,?,?,?,?,?,?,?,?,?)',
                  ('T000','ACCIONES','BYMA','USD','INMEDIATA',STAMP,STAMP,STAMP,STAMP))
    with Store(database,now=NOW) as store:
        p=Projection(store)
        assert p.runtime['market_freshness']=='FRESH'
        rows=p.market_freshness_cohort().rows
    ars,usd=rows[0],rows[1]
    assert ars['currency']=='ARS' and ars['book_freshness']=='STALE' and ars['receipt_freshness']=='FRESH'
    assert usd['currency']=='USD' and usd['book_freshness']=='FRESH'


def test_recent_ingestion_cannot_refresh_old_history_session(database):
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE history_canonical_v2(symbol TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,price_basis TEXT,adjustment_basis TEXT,date TEXT,observed_at TEXT,source TEXT)')
        c.execute("INSERT INTO history_canonical_v2 VALUES('T000','ACCIONES','BYMA','ARS','INMEDIATA','RAW','RAW_NO_ADJUSTMENT','2026-09-28',?,'fixture')",(STAMP,))
    with Store(database,now=NOW) as store:
        row=Projection(store).history().rows[0]
    assert row['ingest_freshness']=='FRESH' and row['freshness']=='STALE'
    assert row['series_last_session']=='2026-09-28' and row['expected_last_session']>'2026-09-28'


def test_analysis_alias_restores_annual_view_and_requires_full_monetary_identity(client):
    response=client.get('/analisis')
    assert response.status_code==200
    assert 'Análisis anual por instrumento' in response.text
    assert BeautifulSoup(response.text,'html.parser').select_one('#terminal-content')['data-view']=='analitica/anual'
    response=client.get('/analitica/anual',params={'identity':'["T000","ACCIONES","BYMA","ARS","INMEDIATA"]'})
    assert 'ANNUAL_FULL_IDENTITY_AND_BASES_REQUIRED' in response.text


def test_annual_report_reads_exact_currency_and_basis_without_legacy_io(client,tmp_path,monkeypatch):
    from datetime import datetime,timezone
    import rc6_annual_instrument_analysis as legacy
    path=tmp_path/'annual.db';at=datetime.now(timezone.utc).isoformat()
    year=datetime.now(timezone.utc).year
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE history_canonical_v2(symbol TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,price_basis TEXT,adjustment_basis TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT,adjusted INTEGER,observed_at TEXT,version_known_at TEXT)')
        for currency,basis,price in [('ARS','RAW',123.45),('USD','RAW',99999),('ARS','ADJUSTED',88888)]:
            c.execute('INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('T000','ACCIONES','BYMA',currency,'INMEDIATA',basis,'RAW_NO_ADJUSTMENT',f'{year}-01-05',price,price,price,price,1,'fixture',0,at,at))
    monkeypatch.setenv('HIST_DB_PATH',str(path))
    monkeypatch.setattr(legacy,'render_page',lambda *a,**kw:pytest.fail('unbounded legacy I/O'))
    response=client.get('/analisis',params={'identity':'["T000","ACCIONES","BYMA","ARS","INMEDIATA"]',
                                          'price_basis':'RAW','adjustment_basis':'RAW_NO_ADJUSTMENT'})
    assert response.status_code==200 and 'Último cierre' in response.text
    assert '123,4500' in response.text and '99999' not in response.text and '88888' not in response.text
    assert 'Moneda ARS' in response.text and 'No habilita operatoria' in response.text
