import json, os, sqlite3
from pathlib import Path
from datetime import datetime, timezone
import pytest

import ck_policy_gate_hf6 as policy
import cp_contract_evidence_v2_hf6 as ce
import cu_history_store_v2_hf6 as hv2
import dd_history_metrics_hf6 as hm
import de_scheduler_catalog_hf6 as sched
import rc4_ppi_contract_normalizer as norm
import rc4_storage_lifecycle_audit as storage
import rc4_a3_cem_history_runner as a3
import rc4_functional_health as fhealth
import da_dashboard_ux_hf6 as ux
import rc4_contract_schedule as csched
import co_contract_ingestion_policy_hf6 as cpolicy
import rc4_contract_import_job as cimport
import rc4_contract_legacy_bridge as lbridge
import rc4_module_inventory as minv
import cr_pending_settlement_diagnostics_hf6 as sdiag

class Store:
    def __init__(self,path): self.path=str(path)
    def connect(self):
        c=sqlite3.connect(self.path); c.row_factory=sqlite3.Row; return c

def test_shadow_evaluates_but_does_not_block(monkeypatch):
    monkeypatch.setenv('PAPER_EXPECTANCY_POLICY','OBSERVATION_ONLY')
    r=policy.evaluate(expectancy_samples=[{'sample_state':'OBSERVATIONAL','empirical_expectancy':'-1','currency':'ARS'}],breadth={'state':'NEUTRAL'},sectors={'groups':[]},candidate_sector='ENERGY')
    assert r['would_block'] is True
    assert r['execute_block'] is False

def test_binding_negative_blocks(monkeypatch):
    monkeypatch.setenv('PAPER_EXPECTANCY_POLICY','BINDING')
    r=policy.evaluate(expectancy_samples=[{'sample_state':'OBSERVATIONAL','empirical_expectancy':'-1','currency':'ARS'}],breadth={'state':'NEUTRAL'},sectors={'groups':[]},candidate_sector='ENERGY')
    assert r['execute_block'] is True
    assert 'EXPECTANCY_NEGATIVE_ARS' in r['verdict']

def test_binding_missing_evidence_is_fail_closed(monkeypatch):
    monkeypatch.setenv('PAPER_SECTOR_CONCENTRATION_POLICY','BINDING')
    r=policy.evaluate(expectancy_samples=[],breadth=None,sectors=None,candidate_sector=None)
    assert r['gates']['sector']['evidence_block'] is True
    assert r['gates']['sector']['execute_block'] is True

def test_contract_identity_alias_and_settlement(tmp_path):
    s=Store(tmp_path/'ce.db')
    ce.record_snapshot(s,family='OBLIGACIONES_NEGOCIABLES',ticker='ON1',market='BYMA',settlement='A-24HS',source_class='PPI_AUTHENTICATED_XHR',source_ref='x',evidence={'currency':'ARS'})
    ce.record_snapshot(s,family='ON',ticker='ON1',market='BYMA',settlement='INMEDIATA',source_class='PPI_AUTHENTICATED_XHR',source_ref='x',evidence={'currency':'ARS'})
    rows=ce.current_records(s,family='ON',ticker='ON1')
    assert {r['settlement'] for r in rows}=={'A-24HS','INMEDIATA'}
    assert {r['family'] for r in rows}=={'ON'}

def test_contract_secret_rejected(tmp_path):
    s=Store(tmp_path/'ce.db')
    with pytest.raises(ValueError,match='SENSITIVE_FIELD'):
        ce.record_snapshot(s,family='BONOS',ticker='X',market='BYMA',settlement='A-24HS',source_class='PPI_AUTHENTICATED_XHR',source_ref='x',evidence={'cookie':'secret'})

def test_contract_never_auto_activates(tmp_path):
    s=Store(tmp_path/'ce.db')
    now=datetime.now(timezone.utc).isoformat()
    ce.record_snapshot(s,family='BONOS',ticker='X',market='BYMA',settlement='A-24HS',source_class='PPI_AUTHENTICATED_XHR',source_ref='x',observed_at=now,evidence={'a':1})
    r=ce.readiness_state(ce.current_records(s,family='BONOS',ticker='X'),required_fields=('a',),max_age_seconds=3600)
    assert r['state']=='READY_PAPER_CANDIDATE'
    assert r['auto_activation_allowed'] is False

def _candle():
    return hv2.Candle('AAA','ACCIONES','BYMA','A-24HS','2026-09-01',100,110,90,105,10,'PPI_PRODUCTION_HISTORY',False,'2026-09-02T20:00:00+00:00',{})

def test_history_identical_version_is_deduped(tmp_path):
    s=Store(tmp_path/'h.db')
    a=hv2.append_candle(s,_candle()); b=hv2.append_candle(s,_candle())
    with s.connect() as c: n=c.execute('SELECT COUNT(*) FROM history_versions_v2').fetchone()[0]
    assert n==1 and a['version_appended'] is True and b['version_appended'] is False

def test_history_file_without_v2_schema_falls_back_legacy(tmp_path):
    h=tmp_path/'market_history.db'; sqlite3.connect(h).close()
    c=sqlite3.connect(':memory:')
    c.execute('CREATE TABLE production_history(symbol TEXT,instrument_type TEXT,row_count INTEGER,date_from TEXT,date_to TEXT)')
    c.execute("INSERT INTO production_history VALUES('GGAL','ACCIONES',100,'2026-01-01','2026-09-01')")
    r=hm.effective_store_metrics(c,h)
    assert r['layer']=='LEGACY_FALLBACK' and r['reason']=='V2_SCHEMA_NOT_PRESENT'
    assert r['by_family']['ACCIONES']['symbols']==1

def test_scheduler_uses_multiple_evidence_sources_and_news_off(monkeypatch):
    monkeypatch.setenv('PAPER_NEWS_INGEST_ENABLED','false')
    # module cadence is import-time; verify semantic helper directly and multi-source separately.
    assert sched._news_cadence_seconds()==12*3600
    rows=sched.internal_rows([],source_sync_rows=[{'source':'PPI_PRODUCTION_HISTORY','last_attempt_at':'2026-09-03T10:00:00+00:00','last_success_at':'2026-09-03T10:00:00+00:00','status':'OK'}],contract_run_rows=[{'job_key':'CONTRACT_EVIDENCE_CAUCIONES','started_at':'2026-09-03T10:00:00+00:00','finished_at':'2026-09-03T10:00:01+00:00','state':'OK'}])
    by={r['key']:r for r in rows}
    assert by['PPI_PRODUCTION_HISTORY']['evidence_table']=='source_sync'
    assert by['CONTRACT_EVIDENCE_CAUCIONES']['evidence_table']=='contract_evidence_runs'

def test_ppi_normalizer_drops_account_quantity_and_does_not_infer_steps():
    p={'payload':[{'ticker':'AAA','cantidadDisponible':999,'cantidadDecimales':0,'cantidadDecimalesPrecio':3,'instrumentosDerivados':[{'private':'x'}]}]}
    r=norm.instrumentos_operables(p)[0]
    assert 'cantidadDisponible' not in r
    assert r['quantity_step'] is None and r['price_tick'] is None
    assert 'instrumentosDerivados' not in r and r['derived_instrument_count']==1

def test_storage_lifecycle_is_audit_only(tmp_path):
    r=storage.audit(tmp_path)
    assert r['mode']=='AUDIT_ONLY' and r['mutation_allowed'] is False
    src=Path(storage.__file__).read_text()
    assert 'docker prune' not in src.lower()
    assert 'os.remove(' not in src and '.unlink(' not in src

def test_a3_is_background_only():
    src=Path(a3.__file__).read_text()
    assert "\"execution_allowed\":False" in src or "\'execution_allowed\':False" in src
    assert 'A3_CEM_CLOSING' in src

def test_settlement_no_time_max():
    src=Path(__file__).parents[1].joinpath('cf_sale_settlement.py').read_text()
    assert 'time.max' not in src

def test_navigation_and_system_sections_and_vivo_contract():
    ux.assert_ux_invariants()
    assert '/scalping' in [x.href for x in ux.TOP_NAV] and '/validacion' in [x.href for x in ux.TOP_NAV]
    dash=Path(__file__).parents[1].joinpath('bg_paper_dashboard.py').read_text()
    assert '"scraping"' in dash.lower() and '"backups"' in dash.lower()
    start=dash.index('def live_page('); end=dash.index('\ndef ',start+5)
    live=dash[start:end]
    assert '_rejection_funnel()' not in live
    assert 'Operaciones abiertas' in live and 'Lección aprendida' in live and 'Scalping' in live
    assert live.index('1. Operaciones abiertas ahora') < live.index('4. Scalping')

def test_trusted_browser_wrapper_has_no_credential_login():
    root=Path(__file__).parents[1]
    wrapper=root.joinpath('scripts/porota_contract_evidence_trusted_rc4.sh').read_text()
    collector=root.joinpath('rc4_trusted_browser_contract_collector.py').read_text()
    # Safety is structural, not a fictional CLI flag.
    assert 'PPI_PASSWORD' not in wrapper and 'PPI_USERNAME' not in wrapper and 'OTP' not in wrapper
    assert '--profile' in wrapper
    assert "SAFE_METHODS={'GET','HEAD','OPTIONS'}" in collector
    assert 'method not in SAFE_METHODS' in collector
    assert 'real_orders_sent' in collector

def test_functional_health_is_read_only_by_contract():
    src=Path(fhealth.__file__).read_text()
    assert "mode=ro" in src and "'db_write':False" in src and "'execution_allowed':False" in src


def test_contract_scheduler_cadence_has_one_canonical_source():
    for job, cadence in csched.JOB_TO_CADENCE.items():
        assert csched.cadence_seconds(job) == cpolicy.ttl_seconds(cadence)


def test_family_readiness_never_auto_activates_even_when_complete(tmp_path):
    s=Store(tmp_path/'family.db')
    ev={'instrument_id':1,'ticker':'AAA','market':'BYMA','currency':'ARS',
        'settlement':'A-24HS','quantity_min':1,'quantity_step':1,
        'price_precision':2,'cost_model':'MODEL_V1'}
    ce.record_snapshot(s,family='ACCIONES',ticker='AAA',market='BYMA',settlement='A-24HS',
        source_class='PPI_AUTHENTICATED_XHR',source_ref='fixture',evidence=ev)
    rows=ce.current_records(s,family='ACCIONES',ticker='AAA')
    r=ce.family_readiness_state(rows,family='ACCIONES',max_age_seconds=3600,
        simulator_ready=True,cost_ready=True)
    assert r['status']=='READY_PAPER_CANDIDATE'
    assert r['auto_activation_allowed'] is False


def test_blocked_auth_capture_is_persisted_as_run_not_contract(tmp_path):
    s=cimport.Store(str(tmp_path/'blocked.db'))
    capture=tmp_path/'capture.json'
    capture.write_text(json.dumps({'jobs':['CONTRACT_EVIDENCE_DYNAMIC'],
        'auth_status':'BLOCKED_AUTH_SESSION_EXPIRED','endpoints':{},'real_orders_sent':0}))
    r=cimport.import_capture(s,capture)
    assert r['state']=='BLOCKED_AUTH' and r['records']==0 and r['real_orders_sent']==0
    with s.connect() as c:
        run=dict(c.execute('SELECT job_key,state,records FROM contract_evidence_v2_runs').fetchone())
        n=c.execute('SELECT COUNT(*) FROM contract_evidence_v2_snapshots').fetchone()[0]
    assert run['state']=='BLOCKED_AUTH' and run['records']==0 and n==0


def test_legacy_bridge_is_metadata_only_and_never_activates(tmp_path):
    s=Store(tmp_path/'legacy.db')
    with s.connect() as c:
        c.execute('CREATE TABLE contract_evidence(instrument_type TEXT,ticker TEXT,market TEXT,status TEXT,owner TEXT,source TEXT,checked_at TEXT,missing_fields_json TEXT,detail TEXT)')
        c.execute('INSERT INTO contract_evidence VALUES(?,?,?,?,?,?,?,?,?)',
            ('BONOS','AE38','BYMA','VERIFIED_EXISTING_PAPER_CONTRACT','PPI','legacy',
             datetime.now(timezone.utc).isoformat(),'[]','legacy row'))
    r=lbridge.bridge(s)
    assert r['recorded']==1 and r['auto_activation_allowed'] is False
    rows=ce.current_records(s,family='BONOS',ticker='AE38')
    assert rows[0]['source_class']=='POROTA_LEGACY_EVIDENCE'
    assert 'quantity_step' not in rows[0]['evidence']


def test_host_backup_source_excludes_secrets_and_records_vector_evidence():
    root=Path(__file__).parents[1]
    src=root.joinpath('rc4_host_general_backup_job.py').read_text()
    under=root.joinpath('scripts/v17_host_general_backup.py').read_text()
    assert 'include_secrets=False' in src
    assert 'sre_vector_db_files=' in src
    assert '.env' in under and 'include_secrets' in under


def test_module_inventory_has_no_unreviewed_orphans():
    rows, collisions=minv.build(Path(__file__).parents[1])
    assert not [r for r in rows if r['status']=='REVIEW_REQUIRED']
    assert isinstance(collisions,dict)


def test_t1_settlement_diagnostics_remains_pending_confirmation():
    r=sdiag.classify_receivable({'paper_id':'P1','ticker':'GGAL','currency':'ARS',
        'settlement':'A-24HS','closed_at':'2026-09-03T18:00:00-03:00',
        'net_proceeds':'1000','available_at':None,'basis':'PENDING_CONFIRMATION'},
        as_of='2026-09-04T10:00:00-03:00')
    assert r['state']=='PENDING_CONFIRMATION'
    assert r['available_at'] is None
    assert r['expected_business_date'] is not None

def test_rc4_emergency_cap_auto_is_quantitatively_derived():
    from de_concurrent_risk_capacity_hf6 import derive_emergency_position_cap
    cap, source = derive_emergency_position_cap(
        soft_stop_pct='1.5', risk_per_trade_fraction='0.002', configured='AUTO')
    assert cap == 8
    assert source == 'DERIVED'
    cap2, source2 = derive_emergency_position_cap(
        soft_stop_pct='1.5', risk_per_trade_fraction='0.002', configured='11')
    assert (cap2, source2) == (11, 'OVERRIDE')

def test_rc4_max_hold_uses_current_session_clock_without_stale_magic_number():
    from rc4_validation import max_hold_effectiveness
    from bq_exit_policy import PaperSessionPolicy
    policy = PaperSessionPolicy()
    open_minutes = policy.open_time.hour * 60 + policy.open_time.minute
    close_minutes = policy.close_time.hour * 60 + policy.close_time.minute
    eod_minutes = close_minutes - open_minutes - policy.exit_minutes
    row = max_hold_effectiveness(360, policy=policy)
    assert row['earliest_open_to_forced_eod_minutes'] == eod_minutes
    assert row['effective_ceiling_minutes'] == min(360, eod_minutes)
    assert row['state'] == ('DOMINATED_BY_EOD' if 360 > eod_minutes else 'REACHABLE')
    assert row['parameter_change_allowed'] is False

def test_rc4_max_hold_reports_when_eod_really_dominates_without_retuning():
    from rc4_validation import max_hold_effectiveness
    from bq_exit_policy import PaperSessionPolicy
    policy = PaperSessionPolicy()
    open_minutes = policy.open_time.hour * 60 + policy.open_time.minute
    close_minutes = policy.close_time.hour * 60 + policy.close_time.minute
    eod_minutes = close_minutes - open_minutes - policy.exit_minutes
    row = max_hold_effectiveness(eod_minutes + 1, policy=policy)
    assert row['earliest_open_to_forced_eod_minutes'] == eod_minutes
    assert row['effective_ceiling_minutes'] == eod_minutes
    assert row['state'] == 'DOMINATED_BY_EOD'
    assert row['parameter_change_allowed'] is False

def test_rc4_paper_fee_reconciliation_never_calls_broker(monkeypatch):
    import h_daily_report as report
    class PPI:
        def get_tax_report(self, *a, **k):
            raise AssertionError('broker tax report must not be queried in PAPER')
    monkeypatch.setenv('ENVIRONMENT','PRODUCTION_PAPER')
    monkeypatch.setenv('ORDER_EXECUTION_MODE','SIMULATED')
    row=report._reconcile_real_costs_today(PPI(), [{'ticker':'GGAL','entry_price':100.0,'quantity':1}])
    assert row['state']=='NOT_OBSERVABLE_IN_PAPER'
    assert row['real_cost_ars'] is None and row['diff_ars'] is None

def test_rc4_sector_map_requires_reviewed_source(tmp_path, monkeypatch):
    import rc4_policy_context as ctx
    p=tmp_path/'sector.csv'
    p.write_text('ticker,family,market,currency,settlement,sector,source,author,effective_at,reviewed\n'
                 'GGAL,ACCIONES,BYMA,ARS,A-24HS,BANKING,MANUAL_REVIEW,a,2026-09-03,false\n'
                 'BBAR,ACCIONES,BYMA,ARS,A-24HS,BANKING,MANUAL_REVIEW,a,2026-09-03,true\n',encoding='utf-8')
    monkeypatch.setenv('POROTA_SECTOR_MAP_PATH',str(p))
    rows=ctx._explicit_sector_map()
    assert ('GGAL','ACCIONES','BYMA','ARS','A-24HS') not in rows
    assert rows[('BBAR','ACCIONES','BYMA','ARS','A-24HS')]['sector']=='BANKING'


def test_rc4_default_sector_map_is_empty_until_reviewed():
    import rc4_policy_context as ctx
    rows=ctx._explicit_sector_map()
    assert rows == {}

def test_rc4_replay_slippage_is_paper_labeled_and_converts_to_bps(tmp_path):
    import sqlite3
    from rc4_replay_analysis import analyze
    db=tmp_path/'paper.db'
    c=sqlite3.connect(db)
    c.execute('CREATE TABLE paper_fills(side TEXT, price TEXT, slippage TEXT)')
    c.execute("INSERT INTO paper_fills VALUES('BUY_SIMULATED','100.02','0.02')")
    c.execute("INSERT INTO paper_fills VALUES('SELL_SIMULATED','99.98','0.02')")
    c.commit(); c.close()
    row=analyze(str(db),history_path=str(tmp_path/'none.db'))['slippage']
    assert row['broker_real_execution'] is False
    assert row['label'].startswith('PAPER_MODELED')
    assert round(row['bps_by_side']['BUY_SIMULATED']['p50'],4)==2.0
    assert round(row['bps_by_side']['SELL_SIMULATED']['p50'],4)==2.0

def test_sre_backups_reuses_complete_coverage_matrix():
    src=Path('bg_paper_dashboard.py').read_text(encoding='utf-8')
    assert 'Backups y restauración — cobertura completa' in src
    assert 'content=backups_content()' in src
    coverage=Path('rc4_backup_coverage.py').read_text(encoding='utf-8')
    for storage in ('observer_v17.db','market_history.db','sre_vector_db','host_general_backup'):
        assert storage in coverage


def test_introspection_superseded_snapshot_uses_live_observer_state(monkeypatch):
    import bg_paper_dashboard as dash
    stale={
        'timestamp':'2026-09-03T00:15:00-03:00',
        'verdict':'WARN',
        'observer':{'process_state':'WAITING_MARKET','session_state':'MARKET_CLOSED','real_orders_sent':0},
        'trading':{},'ingestion':{},'scalping':{},'caucion_readiness':{},
        'market_regime_observation':{},'sector_concentration':{},'storage':{},
    }
    monkeypatch.setattr(dash,'_latest_introspection',lambda:stale)
    monkeypatch.setattr(dash,'_latest_publication_status',lambda:None)
    monkeypatch.setattr(dash,'snapshot',lambda:{'state':{
        'process_state':'RUNNING','session_state':'MARKET_OPEN','heartbeat_at':'2026-09-03T16:37:00+00:00',
        'real_orders_sent':0,'ppi_auth':'OK'}})
    html=dash.introspection_content()
    assert 'RUNNING / MARKET_OPEN' in html
    assert 'SUPERSEDED_BY_LIVE_STATE' in html
    assert 'WAITING_MARKET / MARKET_CLOSED' not in html


def test_scraping_dashboard_exposes_run_evidence_without_promoting_ready(tmp_path,monkeypatch):
    import bg_paper_dashboard as dash
    from be_paper_engine import PaperStore
    db=tmp_path/'scrape.db'
    store=PaperStore(str(db))
    with store.connect() as c:
        c.execute("""CREATE TABLE contract_evidence_v2_runs(
          id INTEGER PRIMARY KEY,job_key TEXT,started_at TEXT,finished_at TEXT,state TEXT,
          records INTEGER,changed INTEGER,conflicts INTEGER,detail TEXT)""")
        c.execute("INSERT INTO contract_evidence_v2_runs VALUES(1,'CONTRACT_EVIDENCE_DYNAMIC','2026-09-03T18:00:00+00:00','2026-09-03T18:00:02+00:00','OK',12,2,1,'read-only XHR; no activation')")
    monkeypatch.setattr(dash,'DB_PATH',str(db))
    html=dash.scraping_content()
    assert 'CONTRACT_EVIDENCE_DYNAMIC' in html
    assert '12' in html and '2' in html and '1' in html
    assert 'read-only XHR; no activation' in html
    assert 'nunca auto-promueve READY_PAPER' in html


def test_deploy_contract_is_split_and_preflight_green():
    import subprocess,sys
    compose=Path('docker-compose.yml').read_text(encoding='utf-8')
    assert 'NO es el contrato canónico de PRODUCTION_PAPER' in compose
    contract=Path('RC4_DEPLOY_CONTRACT.md').read_text(encoding='utf-8')
    assert 'porota_production_observer' in contract
    assert 'porota_production_dashboard' in contract
    assert 'no autoriza deploy' in contract.lower()
    proc=subprocess.run([sys.executable,'rc4_release_preflight.py'],capture_output=True,text=True)
    assert proc.returncode == 0, proc.stdout+proc.stderr
    assert 'RC4_PREFLIGHT=GREEN' in proc.stdout
