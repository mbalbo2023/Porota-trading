"""Adversarial postdeploy evidence: two real time-separated read-only pulses."""
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


host=module('a161_host',ROOT/'scripts/rc6_a161_postdeploy_pulse_ro.py')
comparison=module('a161_compare',ROOT/'scripts/rc6_a161_compare_postdeploy_ro.py')
SHA='a'*40
TREE='b'*40
DEPLOY='c'*40
NOW=datetime(2026,10,28,15,0,tzinfo=timezone.utc)


def publish_shadow_cut(shadow, as_of, phase):
    """Create the exact report/status identity committed by the publisher."""
    def wrapped(payload):
        return json.dumps({'payload':payload,'digest':host.canonical_digest(payload)},
                          sort_keys=True).encode()
    report={'as_of':as_of,'real_orders_sent':0,'real_routes':'NOT_CALLED'}
    status={'as_of':as_of,'status':'SHADOW_OBSERVING','phase':phase,
            'mode':'SHADOW','provider_requests':0,'real_orders_sent':0,
            'real_routes':'NOT_CALLED','configuration_fingerprint':'fixture-config',
            'report_digest':host.canonical_digest(report)}
    ident=host.canonical_digest({'report':report,'status':status})
    watermark={'as_of':as_of}
    metadata={'generation_id':ident,'source_watermark':watermark,
              'configuration_fingerprint':'fixture-config'}
    report={**report,**metadata}
    status={**status,**metadata}
    sequence=int(datetime.fromisoformat(as_of).timestamp()*1000000)
    manifest={'schema':'rc6.dashboard-generation.v1','generation_id':ident,
              'sequence':sequence,'source_watermark':watermark,
              'configuration_fingerprint':'fixture-config',
              'files':{'report':{'payload_digest':host.canonical_digest(report)},
                       'status':{'payload_digest':host.canonical_digest(status)}}}
    pointer={'generation_id':ident,'sequence':sequence,
             'manifest_sha256':host.canonical_digest(manifest),
             'file':'cut-'+ident+'.json.gz'}
    (shadow/'CURRENT.json').write_bytes(wrapped(pointer))
    (shadow/'status.json').write_bytes(wrapped(status))
    (shadow/pointer['file']).write_bytes(gzip.compress(wrapped({
        'manifest':manifest,'status':status,'report':report}),mtime=0))
    return ident,status,sequence


def receipt(at,sequence,scalp,scanner_restarts=53):
    stamp=at.isoformat()
    children={name:{'running':True,'pid_present':True,
                    'restarts':scanner_restarts if name=='scanner' else 0}
              for name in host.ROLES}
    container={name:{'id_sha256':'f'*64,'restarts':0,'running':True,
                     'oom_killed':False}
               for name in ('observer','dashboard','critical')}
    policy=json.loads((ROOT/'ops/policy/host-control-plane-reconciliation-v2.json').read_text())
    names=sorted(Path(path).name for path,row in policy['units'].items()
                 if row.get('role')=='ACTIVE_TIMER' and row.get('active') is True)
    states={name:{'state':'inactive' if name=='porota-preopen-rc6.timer' else 'active',
                  'enabled':'disabled' if name=='porota-preopen-rc6.timer' else 'enabled',
                  'migration_expected':True,
                  'service_result':'EXIT_CODE' if
                      name=='porota-local-eod-history-rc6.timer' else 'SUCCESS'}
            for name in names}
    return {'schema':'rc6.a161.postdeploy-ro.v1','status':'OPEN_PROVISIONAL',
            'audit_phase':'OPEN',
            'at_utc':stamp,'candidate_sha':SHA,'candidate_tree':TREE,
            'deploy_sha':DEPLOY,'droplet':host.EXPECTED,
            'host_mutations':0,'iol_provider_requests':0,'real_orders_sent':0,
            'operational_go':False,
            'release':{'validation':'VALIDATED_RUNTIME','image_id':'sha256:'+'1'*64,
                       'frozen_manifest_sha256':'d'*64,
                       'frozen_preload_image_id':'sha256:'+'e'*64,
                       'validated_at_utc':'2026-10-28T14:00:00+00:00'},
            'critical':'ISSUES_ONLY_HEALTHY','dashboard_http':200,
            'dashboard':{'mode':'PRODUCTION_PAPER','execution':'SIMULATED',
                         'real_orders_sent':0,'ready':2,'families':{'ACCIONES':2},
                         'process_state':'RUNNING','session_state':'MARKET_OPEN',
                         'market_open':True},
            'paper_interlock':{'allow_new_openings':False,
                               'reason':'OPERATOR_PAPER_PAUSE_2026_10_28'},
            'containers':container,'runtime':{'children':children},
            'paper':{'mode':'PRODUCTION_PAPER','flat_books':3,'real_orders_sent':0,
                     'observer_state':'RUNNING','session_state':'MARKET_OPEN',
                     'ppi_auth':'OK',
                     'integrity':'PRAGMA_QUICK_CHECK_OK' if sequence==1 else 'IN_FIRST_PULSE',
                     'scalping':{'heartbeat_at':scalp,'failed_cumulative':0,
                                 'state':'RUNNING'},
                     'caucion':{'heartbeat_at':scalp,'state':'WAITING_WINDOW'},
                     'exit_supervisor':{'heartbeat_at':scalp,'heartbeat_age_s':0,
                                        'state':'RUNNING'},
                     'exit_reader':{'heartbeat_at':scalp,'heartbeat_age_s':0,
                                    'state':'READY'},
                     'readiness_by_family':{'ACCIONES':2},
                     'observer_heartbeat_at':scalp},
            'shadow':{'generation_id':str(sequence)*64,'sequence':sequence,
                      'as_of':stamp,'phase':'OPEN','digest_verified':True},
            'iol':{'cache_state':'LIVE_FRESH','guardian_age_s':600,
                   'cache_observed_age_s':90,'cache_refreshed_age_s':90},
            'timers':{'count':16,'active_enabled':15,
                      'operator_preopen_disabled':True,'iol_guard_active':True,
                      'all_healthy':False,
                      'failed_units':['porota-local-eod-history-rc6.timer'],
                      'historical_eod_a162_only':True,
                      'historical_cause_run_id':40722096423,
                      'historical_cause_artifact_id':12830702310,
                      'historical_cause_artifact_sha256':
                          '7f0d8950527fbaef8fd6a0e2ce619c16a3efee385fa98a4f4e4808d329379f83',
                      'historical_eod_invocation_id':'e'*32,
                      'eod_next_elapse_utc':'2026-10-28T20:15:00+00:00',
                      'states':states},
            'ppi_watch':{'state':'NOT_PRESENT','unit_count':0,
                         'snapshot_sha256':'d'*64},'disk_free_bytes':3*1024**3,
            'disk_inode_free_percent':70,'disk_inode_floor_percent':10}


class HostTests(unittest.TestCase):
    def test_dashboard_aggregate_source_is_bounded_and_contains_no_token(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,_):return b'ok'
        class Opener:
            def open(self,*_,**__):return Response()
        summary={'mode':'PRODUCTION_PAPER','execution':'SIMULATED',
                 'process_state':'WAITING_MARKET','session_state':'MARKET_CLOSED',
                 'real_orders_sent':0,'market_open':False,'scalping_mode':'ACTIVE_PAPER',
                 'history_governs_readiness':False,
                 'readiness_source':'candidate_identity_v2','ready':1,
                 'families':{'ACCIONES':1}}
        def call(args,seconds):
            self.assertEqual(args[:4],['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1'])
            self.assertEqual(args[4:7],['porota_production_dashboard','python','-B'])
            self.assertEqual(seconds,65)
            compile(args[-1],'<dashboard-truth-aggregate>','exec')
            self.assertNotIn('secret',args[-1].lower())
            return json.dumps(summary)
        with patch.object(host.urllib.request,'build_opener',return_value=Opener()), \
             patch.object(host,'command',side_effect=call):
            self.assertEqual(host.dashboard(),summary)
            summary['real_orders_sent']=True
            with self.assertRaisesRegex(ValueError,'DASHBOARD_TRUTH_SAFETY_RED'):
                host.dashboard()

    def test_frozen_manifest_identity_uses_exact_bytes_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'porota-frozen-candidate.json'
            frozen={'candidate_sha':SHA,'candidate_tree_sha':TREE,
                    'image_id':'sha256:'+'d'*64}
            raw=json.dumps(frozen,sort_keys=True).encode()
            path.write_bytes(raw)
            value,digest=host.exact_json(path,16384,with_digest=True)
            self.assertEqual(value,frozen)
            self.assertEqual(digest,hashlib.sha256(raw).hexdigest())
            path.write_bytes(raw+b' ')
            self.assertNotEqual(host.exact_json(path,16384,with_digest=True)[1],digest)

    def result(self,*args):
        out=io.StringIO()
        with redirect_stdout(out):rc=host.pulse(*args)
        lines=out.getvalue().splitlines()
        self.assertEqual(len(lines),1)
        return rc,json.loads(lines[0].split('=',1)[1])

    def test_wrong_droplet_rejects_before_lock_and_all_runtime_reads(self):
        with patch.object(host,'metadata',side_effect=['594077618','nyc1']), \
             patch.object(host,'lock_existing',side_effect=AssertionError('LOCK_TOUCHED')):
            rc,value=self.result(SHA,TREE,DEPLOY,'check')
        self.assertEqual(rc,2)
        self.assertEqual(value['error_code'],'DROPLET_IDENTITY_MISMATCH')
        self.assertNotIn('release',value)

    def test_lock_existing_read_only_barrier_presence_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/'lock';barrier=Path(d)/'barrier'
            lock.write_bytes(b'no-content-change');lock.chmod(0o600)
            original=(lock.read_bytes(),lock.stat().st_mtime_ns)
            with patch.object(host,'LOCK',lock), patch.object(host,'BARRIER',barrier), \
                 patch.object(host.os,'geteuid',return_value=0):
                fd=host.lock_existing();os.close(fd)
                self.assertEqual(original,(lock.read_bytes(),lock.stat().st_mtime_ns))
                barrier.symlink_to(Path(d)/'missing-target')
                with self.assertRaisesRegex(ValueError,'BARRIER_PRIOR_ATTEMPT'):
                    host.lock_existing()
                barrier.unlink();lock.unlink()
                with self.assertRaises(FileNotFoundError):host.lock_existing()
                self.assertFalse(lock.exists())

    def test_shadow_digest_rejects_tamper_and_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'CURRENT.json'
            payload={'generation_id':'1'*64,'sequence':3}
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True,
                     separators=(',',':')).encode('ascii')).hexdigest()
            path.write_text(json.dumps({'payload':payload,'digest':digest}))
            self.assertEqual(host.envelope(path,4096),payload)
            path.write_text(json.dumps({'payload':payload,'digest':'0'*64}))
            with self.assertRaisesRegex(ValueError,'SHADOW_DIGEST_RED'):
                host.envelope(path,4096)
            path.write_text('{"payload":{},"payload":{},"digest":"'+'0'*64+'"}')
            with self.assertRaisesRegex(ValueError,'JSON_DUPLICATE_KEY_RED'):
                host.envelope(path,4096)

    def test_shadow_requires_coherent_immutable_cut_and_live_status(self):
        def wrapped(payload):
            return json.dumps({'payload':payload,'digest':host.canonical_digest(payload)},
                              sort_keys=True).encode()
        with tempfile.TemporaryDirectory() as d:
            artifacts=Path(d)
            shadow=artifacts/'dynamic-shadow';shadow.mkdir()
            _,status,sequence=publish_shadow_cut(shadow,NOW.isoformat(),'OPEN')
            with patch.object(host,'ARTIFACTS',artifacts):
                result=host.shadow(NOW)
                self.assertEqual(result['sequence'],sequence)
                live=dict(status,status='FAIL_CLOSED')
                (shadow/'status.json').write_bytes(wrapped(live))
                with self.assertRaisesRegex(ValueError,'SHADOW_COMMITTED_GENERATION_RED'):
                    host.shadow(NOW)

    def test_shadow_rejects_coherently_rehashed_but_false_generation_id(self):
        def wrapped(payload):
            return json.dumps({'payload':payload,'digest':host.canonical_digest(payload)},
                              sort_keys=True).encode()
        with tempfile.TemporaryDirectory() as d:
            shadow=Path(d)/'dynamic-shadow';shadow.mkdir()
            ident,_,_=publish_shadow_cut(shadow,NOW.isoformat(),'OPEN')
            cut=json.loads(gzip.decompress((shadow/('cut-'+ident+'.json.gz')).read_bytes()))['payload']
            fake='f'*64
            cut['manifest']['generation_id']=fake
            for role in ('report','status'):
                cut[role]['generation_id']=fake
                cut['manifest']['files'][role]['payload_digest']=host.canonical_digest(cut[role])
            pointer=json.loads((shadow/'CURRENT.json').read_bytes())['payload']
            pointer.update(generation_id=fake,file='cut-'+fake+'.json.gz',
                           manifest_sha256=host.canonical_digest(cut['manifest']))
            (shadow/'CURRENT.json').write_bytes(wrapped(pointer))
            (shadow/'status.json').write_bytes(wrapped(cut['status']))
            (shadow/pointer['file']).write_bytes(gzip.compress(wrapped(cut),mtime=0))
            with patch.object(host,'ARTIFACTS',Path(d)):
                with self.assertRaisesRegex(ValueError,'SHADOW_COMMITTED_GENERATION_RED'):
                    host.shadow(NOW)

    def test_closed_shadow_accepts_only_fresh_cut_or_live_idle_heartbeat(self):
        def wrapped(payload):
            return json.dumps({'payload':payload,'digest':host.canonical_digest(payload)},
                              sort_keys=True).encode()
        with tempfile.TemporaryDirectory() as d:
            artifacts=Path(d);shadow=artifacts/'dynamic-shadow';shadow.mkdir()
            publish_shadow_cut(shadow,NOW.isoformat(),'CLOSED')
            with patch.object(host,'ARTIFACTS',artifacts):
                self.assertEqual(host.shadow(NOW,'CLOSED')['status'],
                                 'SHADOW_CLOSED_ADVANCING')
                ident,_,_=publish_shadow_cut(
                    shadow,(NOW-timedelta(minutes=10)).isoformat(),'CLOSED')
                idle={'schema':'rc6.shadow-closed-idle.v1',
                      'state':'SHADOW_IDLE_MARKET_CLOSED',
                      'source_freshness_renewed':False,
                      'last_generation_id':ident,
                      'last_source_as_of':(NOW-timedelta(minutes=10)).isoformat(),
                      'heartbeat_at':NOW.isoformat(),
                      'real_orders_sent':0,'real_routes':'NOT_CALLED'}
                (shadow/'idle.json').write_bytes(wrapped(idle))
                self.assertEqual(host.shadow(NOW,'CLOSED')['status'],
                                 'SHADOW_IDLE_MARKET_CLOSED')
                idle['source_freshness_renewed']=True
                (shadow/'idle.json').write_bytes(wrapped(idle))
                with self.assertRaisesRegex(ValueError,'SHADOW_IDLE_PROOF_RED'):
                    host.shadow(NOW,'CLOSED')

    def test_stale_iol_cache_cannot_pass_even_with_live_fresh_label(self):
        guardian={'schema':'POROTA_RC6_IOL_OAUTH_GUARDIAN_STATE_V1',
                  'state':'AUTHENTICATED','checked_at':(NOW-timedelta(seconds=10)).isoformat()}
        cache={'cache_state':'LIVE_FRESH',
               'continuation_state':'CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL',
               'source':'IOL_MCP','decision_effect':'OBSERVE_ONLY',
               'real_money_authorized':False,
               'fallback_order':['IOL_LIVE_BOUNDED_RETRY','IOL_LAST_KNOWN_GOOD',
                                 'PPI_PRIMARY','BYMA_PUBLIC_COMPLEMENTARY'],
               'section_states':{'bonos':'LIVE_FRESH'},'live_sections':['bonos'],
               'refreshed_at':(NOW-timedelta(hours=18)).isoformat()}
        with patch.object(host,'exact_json',side_effect=[guardian,cache]):
            with self.assertRaisesRegex(ValueError,'TIMESTAMP_STALE_OR_INVALID'):
                host.iol(NOW)

    def test_guardian_30_minute_timer_with_fresh_collector_is_healthy(self):
        guardian={'schema':'POROTA_RC6_IOL_OAUTH_GUARDIAN_STATE_V1',
                  'state':'ACTIVE_COLLECTOR_HEALTHY',
                  'checked_at':(NOW-timedelta(minutes=10)).isoformat()}
        cache={'cache_state':'LIVE_FRESH',
               'continuation_state':'CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL',
               'source':'IOL_MCP','decision_effect':'OBSERVE_ONLY',
               'real_money_authorized':False,
               'fallback_order':['IOL_LIVE_BOUNDED_RETRY','IOL_LAST_KNOWN_GOOD',
                                 'PPI_PRIMARY','BYMA_PUBLIC_COMPLEMENTARY'],
               'section_states':{'bonos':'LIVE_FRESH'},'live_sections':['bonos'],
               'last_known_good_at':(NOW-timedelta(seconds=110)).isoformat(),
               'refreshed_at':(NOW-timedelta(seconds=95)).isoformat()}
        with patch.object(host,'exact_json',side_effect=[guardian,cache]):
            result=host.iol(NOW)
        self.assertEqual(result['guardian_age_s'],600)
        self.assertEqual(result['cache_observed_age_s'],110)

    def test_historical_failed_timer_service_is_reported_red(self):
        policy=json.loads((ROOT/'ops/policy/host-control-plane-reconciliation-v2.json').read_text())
        def systemd(name,properties):
            if name.endswith('.timer'):
                held=name=='porota-preopen-rc6.timer'
                return {'ActiveState':'inactive' if held else 'active',
                        'UnitFileState':'disabled' if held else 'enabled',
                        'Unit':name.removesuffix('.timer')+'.service'}
            failed=name=='porota-local-eod-history-rc6.service'
            return {'ActiveState':'failed' if failed else 'inactive',
                    'Result':'exit-code' if failed else 'success',
                    'ExecMainStatus':'1' if failed else '0'}
        with patch.object(host,'systemd',side_effect=systemd):
            result=host.timers(policy)
        self.assertFalse(result['all_healthy'])
        self.assertEqual(result['failed_units'],['porota-local-eod-history-rc6.timer'])
        self.assertEqual(result['states'][result['failed_units'][0]]['service_result'],
                         'EXIT_CODE')

    def test_documented_reset_can_preserve_last_exit_without_false_red(self):
        policy=json.loads((ROOT/'ops/policy/host-control-plane-reconciliation-v2.json').read_text())
        def systemd(name,properties):
            if name.endswith('.timer'):
                held=name=='porota-preopen-rc6.timer'
                return {'ActiveState':'inactive' if held else 'active',
                        'UnitFileState':'disabled' if held else 'enabled',
                        'Unit':name.removesuffix('.timer')+'.service'}
            return {'ActiveState':'inactive','Result':'success',
                    'ExecMainStatus':'2' if name=='porota-preopen-rc6.service' else '0'}
        with patch.object(host,'systemd',side_effect=systemd):
            result=host.timers(policy)
        self.assertTrue(result['all_healthy'])
        self.assertEqual(result['failed_units'],[])
        self.assertEqual(result['states']['porota-preopen-rc6.timer'][
            'last_exec_status_historical'],2)

    def test_open_provisional_exact_old_eod_and_today_armed_timer(self):
        policy=json.loads((ROOT/'ops/policy/host-control-plane-reconciliation-v2.json').read_text())
        schedule='Wed 2026-10-28 20:15:00 UTC'
        invocation='e'*32
        def systemd(name,properties):
            if properties==('NextElapseUSecRealtime',):
                return {'NextElapseUSecRealtime':schedule}
            if name.endswith('.timer'):
                held=name=='porota-preopen-rc6.timer'
                return {'ActiveState':'inactive' if held else 'active',
                        'UnitFileState':'disabled' if held else 'enabled',
                        'Unit':name.removesuffix('.timer')+'.service'}
            failed=name=='porota-local-eod-history-rc6.service'
            row={'ActiveState':'failed' if failed else 'inactive',
                 'Result':'exit-code' if failed else 'success',
                 'ExecMainStatus':'2' if failed else '0'}
            if 'InvocationID' in properties:
                row.update(ExecMainStartTimestamp='Tue 2026-10-27 22:10:13 UTC',
                           ExecMainExitTimestamp='Tue 2026-10-27 22:10:13 UTC',
                           InvocationID=invocation)
            return row
        with patch.object(host,'systemd',side_effect=systemd):
            result=host.timers(policy,allow_historical_eod=True,now=NOW)
        self.assertFalse(result['all_healthy'])
        self.assertTrue(result['historical_eod_a162_only'])
        self.assertEqual(result['historical_eod_invocation_id'],'e'*32)
        schedule='Thu 2026-10-29 20:15:00 UTC'
        with patch.object(host,'systemd',side_effect=systemd):
            result=host.timers(policy,allow_historical_eod=True,now=NOW)
        self.assertFalse(result['historical_eod_a162_only'])
        schedule='Wed 2026-10-28 20:15:00 UTC'
        invocation='bad-id'
        with patch.object(host,'systemd',side_effect=systemd):
            result=host.timers(policy,allow_historical_eod=True,now=NOW)
        self.assertFalse(result['historical_eod_a162_only'])

    def test_bounded_live_db_integrity_and_three_book_paper_safety(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'observer.db'
            with sqlite3.connect(path) as db:
                db.executescript('''
                    CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,
                        ppi_auth TEXT,
                        heartbeat_at TEXT,process_state TEXT,session_state TEXT);
                    CREATE TABLE paper_positions(status TEXT);
                    CREATE TABLE paper_cauciones(status TEXT);
                    CREATE TABLE paper_future_positions(status TEXT);
                    CREATE TABLE intraday_scalping_worker_state(id INTEGER,state TEXT,
                        heartbeat_at TEXT,real_orders_sent INTEGER,failed INTEGER);
                    CREATE TABLE paper_caucion_cash_sweep_state(id INTEGER,state TEXT,
                        heartbeat_at TEXT,real_orders_sent INTEGER,routes_json TEXT);
                    CREATE TABLE paper_supervisor_state(id INTEGER,state TEXT,
                        heartbeat_at TEXT);
                    CREATE TABLE paper_exit_reader_state(id INTEGER,state TEXT,
                        heartbeat_at TEXT);
                    CREATE TABLE candidate_identity_v2(instrument_type TEXT,
                        can_simulate INTEGER,status TEXT);
                ''')
                stamp=NOW.isoformat()
                db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'OK',?,'RUNNING','MARKET_OPEN')",(stamp,))
                db.execute("INSERT INTO intraday_scalping_worker_state VALUES(1,'RUNNING',?,0,0)",(stamp,))
                db.execute("INSERT INTO paper_caucion_cash_sweep_state VALUES(1,'WAITING_WINDOW',?,0,'[]')",(stamp,))
                db.execute("INSERT INTO paper_supervisor_state VALUES(1,'RUNNING',?)",(stamp,))
                db.execute("INSERT INTO paper_exit_reader_state VALUES(1,'READY',?)",(stamp,))
                db.execute("INSERT INTO candidate_identity_v2 VALUES('ACCIONES',1,'AVAILABLE')")
            with patch.object(host,'DB',path):
                before=path.stat().st_mtime_ns
                result=host.sqlite_snapshot(NOW,'check')
                self.assertEqual(result['integrity'],'PRAGMA_QUICK_CHECK_OK')
                self.assertEqual(result['flat_books'],3)
                self.assertEqual(result['readiness_by_family'],{'ACCIONES':1})
                self.assertEqual(before,path.stat().st_mtime_ns)
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE observer_state SET process_state='WAITING_MARKET',"
                               "session_state='MARKET_CLOSED'")
                    db.execute("UPDATE intraday_scalping_worker_state SET state='WAITING_MARKET'")
                    db.execute("UPDATE paper_exit_reader_state SET state='WAITING_MARKET'")
                closed=host.sqlite_snapshot(NOW,'reuse','CLOSED')
                self.assertEqual(closed['observer_state'],'WAITING_MARKET')
                self.assertEqual(closed['scalping']['state'],'WAITING_MARKET')
                self.assertEqual(closed['caucion']['state'],'WAITING_WINDOW')
                with sqlite3.connect(path) as db:
                    db.execute("INSERT INTO paper_cauciones VALUES('OPEN')")
                with self.assertRaisesRegex(ValueError,'PAPER_THREE_BOOKS_NOT_FLAT'):
                    host.sqlite_snapshot(NOW,'reuse','CLOSED')

    def test_closed_worker_roles_reject_open_phase_and_broken_exit_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'observer.db'
            with sqlite3.connect(path) as db:
                db.executescript('''
                  CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,
                    ppi_auth TEXT,heartbeat_at TEXT,process_state TEXT,session_state TEXT);
                  CREATE TABLE paper_positions(status TEXT);
                  CREATE TABLE paper_cauciones(status TEXT);
                  CREATE TABLE paper_future_positions(status TEXT);
                  CREATE TABLE intraday_scalping_worker_state(id INTEGER,state TEXT,
                    heartbeat_at TEXT,real_orders_sent INTEGER,failed INTEGER);
                  CREATE TABLE paper_caucion_cash_sweep_state(id INTEGER,state TEXT,
                    heartbeat_at TEXT,real_orders_sent INTEGER,routes_json TEXT);
                  CREATE TABLE paper_supervisor_state(id INTEGER,state TEXT,heartbeat_at TEXT);
                  CREATE TABLE paper_exit_reader_state(id INTEGER,state TEXT,heartbeat_at TEXT);
                  CREATE TABLE candidate_identity_v2(instrument_type TEXT,
                    can_simulate INTEGER,status TEXT);
                ''')
                at=NOW.isoformat()
                db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'OK',?,'WAITING_MARKET','MARKET_CLOSED')",(at,))
                db.execute("INSERT INTO intraday_scalping_worker_state VALUES(1,'WAITING_MARKET',?,0,0)",(at,))
                db.execute("INSERT INTO paper_caucion_cash_sweep_state VALUES(1,'WAITING_WINDOW',?,0,'[]')",(at,))
                db.execute("INSERT INTO paper_supervisor_state VALUES(1,'RUNNING',?)",(at,))
                db.execute("INSERT INTO paper_exit_reader_state VALUES(1,'WAITING_MARKET',?)",(at,))
            with patch.object(host,'DB',path):
                self.assertEqual(host.sqlite_snapshot(NOW,'reuse','CLOSED')[
                    'observer_state'],'WAITING_MARKET')
                with self.assertRaisesRegex(ValueError,'OBSERVER_PHASE_RED'):
                    host.sqlite_snapshot(NOW,'reuse','OPEN')
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE paper_caucion_cash_sweep_state SET state='RUNNING'")
                with self.assertRaisesRegex(ValueError,'WORKER_PAPER_SAFETY_RED'):
                    host.sqlite_snapshot(NOW,'reuse','CLOSED')
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE paper_caucion_cash_sweep_state SET state='WAITING_WINDOW'")
                    db.execute("UPDATE paper_supervisor_state SET heartbeat_at=?",
                               ((NOW-timedelta(seconds=21)).isoformat(),))
                with self.assertRaisesRegex(ValueError,'TIMESTAMP_STALE_OR_INVALID'):
                    host.sqlite_snapshot(NOW,'reuse','CLOSED')


class ComparisonTests(unittest.TestCase):
    def pair(self):
        first=receipt(NOW,1,NOW.isoformat())
        second=receipt(NOW+timedelta(seconds=196),2,
                       (NOW+timedelta(seconds=192)).isoformat())
        return first,second

    def test_progress_and_no_go(self):
        a,b=self.pair()
        result=comparison.compare(a,b,SHA,TREE,DEPLOY)
        self.assertEqual(result['status'],'PROVISIONAL')
        self.assertFalse(result['operational_go'])
        self.assertEqual(result['separation_seconds'],196)

    def test_not_190s_and_no_scalping_advance_fail(self):
        a,b=self.pair()
        b['at_utc']=(NOW+timedelta(seconds=180)).isoformat()
        with self.assertRaisesRegex(ValueError,'PULSES_NOT_190S_APART'):
            comparison.compare(a,b,SHA,TREE,DEPLOY)
        a,b=self.pair();b['paper']['scalping']['heartbeat_at']=a['paper']['scalping']['heartbeat_at']
        with self.assertRaisesRegex(ValueError,'WORKER_HEARTBEAT_NOT_ADVANCED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY)

    def test_scanner_restart_and_shadow_stall_fail(self):
        a,b=self.pair();b['runtime']['children']['scanner']['restarts']+=1
        with self.assertRaisesRegex(ValueError,'CHILD_RESTART_OR_HEALTH_RED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY)
        a,b=self.pair();b['shadow']['generation_id']=a['shadow']['generation_id']
        with self.assertRaisesRegex(ValueError,'SHADOW_GENERATION_NOT_ADVANCED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY)

    def test_release_or_owned_timer_drift_cannot_pass_but_foreign_ppi_may_change(self):
        a,b=self.pair();b['ppi_watch']['snapshot_sha256']='0'*64
        self.assertEqual(comparison.compare(a,b,SHA,TREE,DEPLOY)['status'],'PROVISIONAL')
        a,b=self.pair();b['timers']['states']['porota-preopen-rc6.timer']['enabled']='enabled'
        with self.assertRaisesRegex(ValueError,'RUNTIME_CONTROL_DRIFT_RED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY)
        a,b=self.pair();b['release']['validation']='DEPLOYED_VALIDATION_PENDING'
        with self.assertRaisesRegex(ValueError,'PULSE_2_REQUIRED_GATE_RED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY)

    def test_iol_fresh_label_with_old_receipt_cannot_pass(self):
        a,b=self.pair();b['iol']['cache_refreshed_age_s']=1000
        with self.assertRaisesRegex(ValueError,'OPEN_PROVISIONAL_EXACT_HISTORICAL_EOD_RED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY)

    def test_closed_diagnostic_eod_cannot_become_runtime_green(self):
        a,b=self.closed_pair()
        a['status']='CLOSED_DIAGNOSTIC_RED'
        a['timers']['all_healthy']=False
        a['timers']['failed_units']=['porota-local-eod-history-rc6.timer']
        with self.assertRaisesRegex(ValueError,'PULSE_1_REQUIRED_GATE_RED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY,'CLOSED')

    def test_closed_no_go_and_dashboard_drift_are_blocking(self):
        a,b=self.closed_pair()
        b['paper_interlock']['allow_new_openings']=True
        with self.assertRaisesRegex(ValueError,'PULSE_2_REQUIRED_GATE_RED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY,'CLOSED')
        a,b=self.closed_pair()
        b['dashboard']['ready']=3
        with self.assertRaisesRegex(ValueError,'PAPER_WORKERS_OR_DASHBOARD_RED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY,'CLOSED')
        a,b=self.closed_pair()
        b['paper']['exit_reader']['heartbeat_at']=a['paper']['exit_reader']['heartbeat_at']
        with self.assertRaisesRegex(ValueError,'WORKER_HEARTBEAT_NOT_ADVANCED'):
            comparison.compare(a,b,SHA,TREE,DEPLOY,'CLOSED')

    def closed_pair(self,route='IDLE'):
        first_at=datetime(2026,10,28,21,10,tzinfo=timezone.utc)
        second_at=first_at+timedelta(seconds=196)
        a=receipt(first_at,3,first_at.isoformat())
        b=receipt(second_at,4,second_at.isoformat())
        a['paper']['integrity']='PRAGMA_QUICK_CHECK_OK'
        b['paper']['integrity']='IN_FIRST_PULSE'
        for row,at in ((a,first_at),(b,second_at)):
            row['status']='CLOSED_PROVISIONAL';row['audit_phase']='CLOSED'
            row['paper']['session_state']='MARKET_CLOSED'
            row['paper']['observer_state']='WAITING_MARKET'
            row['paper']['scalping']['state']='WAITING_MARKET'
            row['paper']['exit_reader']['state']='WAITING_MARKET'
            row['dashboard']['process_state']='WAITING_MARKET'
            row['dashboard']['session_state']='MARKET_CLOSED'
            row['dashboard']['market_open']=False
            row['timers']['all_healthy']=True
            row['timers']['failed_units']=[]
            row['timers']['historical_eod_a162_only']=False
            row['timers']['historical_cause_run_id']=None
            row['timers']['historical_cause_artifact_id']=None
            row['timers']['historical_cause_artifact_sha256']=None
            row['timers']['historical_eod_invocation_id']=None
            row['timers']['eod_next_elapse_utc']=None
            row['timers']['states']['porota-local-eod-history-rc6.timer'][
                'service_result']='SUCCESS'
            row['iol']['cache_state']='CACHE_FRESH'
            row['iol']['cache_observed_age_s']=1800
            row['iol']['cache_refreshed_age_s']=1800
            row['shadow']['phase']='CLOSED'
            if route=='IDLE':
                row['shadow'].update(status='SHADOW_IDLE_MARKET_CLOSED',
                    generation_id='3'*64,sequence=3,
                    source_as_of='2026-10-28T20:30:00+00:00',
                    idle_heartbeat_at=at.isoformat(),
                    idle_heartbeat_age_s=0,source_freshness_renewed=False)
            else:
                row['shadow'].update(status='SHADOW_CLOSED_ADVANCING',
                    generation_id=str(3 if row is a else 4)*64,
                    sequence=3 if row is a else 4,
                    as_of=at.isoformat(),age_s=0)
        return a,b

    def closures(self):
        common={'schema':'rc6.a163.unit-closure.v1','status':'GREEN',
                'candidate_sha':SHA,'candidate_tree':TREE,'deploy_sha':DEPLOY,
                'frozen_manifest_sha256':'d'*64,
                'frozen_preload_image_id':'sha256:'+'e'*64,
                'droplet':host.EXPECTED,'operational_go':False,
                'real_orders_sent':0,'ppi_watch_accessed':False,
                'forensics':{'refined_run':40722096423,
                             'refined_artifact_id':12830702310,
                             'refined_artifact_sha256':
                                 '7f0d8950527fbaef8fd6a0e2ce619c16a3efee385fa98a4f4e4808d329379f83'}}
        pre=dict(common,phase='PREOPEN',at_utc='2026-10-28T14:45:00+00:00',
                 host_mutations=1,
                 closure='HISTORICAL_FAILURE_DOCUMENTED_AND_RESET_ONE_UNIT')
        eod=dict(common,phase='EOD',at_utc='2026-10-28T20:20:00+00:00',
                 host_mutations=0,closure='NEW_SUCCESSFUL_SCHEDULED_EOD_PROVEN',
                 eod={'state':'LOCAL_EOD_READY','durable_cursor':True,
                      'hot_highwater_equals_cursor':True,
                      'invocation_id':'f'*32,
                      'timer_last_trigger_utc':'2026-10-28T20:15:00+00:00'})
        return pre,eod

    def test_composed_green_only_after_open_and_real_postclose_idle(self):
        a,b=self.pair();c,d=self.closed_pair();pre,eod=self.closures()
        result=comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)
        self.assertEqual(result['status'],'GREEN')
        self.assertEqual(result['open_provisional']['status'],'PROVISIONAL')
        self.assertFalse(result['operational_go'])
        c['ppi_watch']['snapshot_sha256']='0'*64
        self.assertEqual(comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)['status'],
                         'GREEN')

    def test_composed_closed_advancing_cuts_require_real_progress(self):
        a,b=self.pair();c,d=self.closed_pair('CUT');pre,eod=self.closures()
        self.assertEqual(comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)[
            'closed_provisional']['native_shadow_evidence'],'ADVANCING_CLOSED_CUTS')
        d['shadow']['generation_id']=c['shadow']['generation_id']
        with self.assertRaisesRegex(ValueError,'SHADOW_CLOSED_CUT_NOT_ADVANCED'):
            comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)

    def test_composition_rejects_old_cut_backlog_new_failure_and_sha_drift(self):
        a,b=self.pair();c,d=self.closed_pair();pre,eod=self.closures()
        c['shadow']['source_as_of']='2026-10-27T20:30:00+00:00'
        d['shadow']['source_as_of']=c['shadow']['source_as_of']
        with self.assertRaisesRegex(ValueError,'COMPOSITE_SHADOW_POSTOPEN_SOURCE_RED'):
            comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)
        c,d=self.closed_pair();eod['eod']['hot_highwater_equals_cursor']=False
        with self.assertRaisesRegex(ValueError,'COMPOSITE_NEW_SCHEDULED_EOD_RED'):
            comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)
        _,eod=self.closures();eod['candidate_sha']='0'*40
        with self.assertRaisesRegex(ValueError,'COMPOSITE_ADMIN_CLOSURE_RED'):
            comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)
        _,eod=self.closures();eod['frozen_manifest_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'COMPOSITE_ADMIN_CLOSURE_RED'):
            comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)
        _,eod=self.closures();c['timers']['states'][
            'porota-local-eod-history-rc6.timer']['service_result']='EXIT_CODE'
        d['timers']['states'][
            'porota-local-eod-history-rc6.timer']['service_result']='EXIT_CODE'
        with self.assertRaisesRegex(ValueError,'COMPOSITE_EOD_SERVICE_TRANSITION_RED'):
            comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)
        c,d=self.closed_pair();eod['eod']['invocation_id']='e'*32
        with self.assertRaisesRegex(ValueError,'COMPOSITE_NEW_SCHEDULED_EOD_RED'):
            comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)

    def test_open_closed_from_different_business_days_cannot_compose(self):
        a,b=self.pair();c,d=self.closed_pair();pre,eod=self.closures()
        for row in (c,d):
            row['at_utc']=(comparison.date(row['at_utc'])+timedelta(days=1)).isoformat()
            beat=row['at_utc']
            for path in (('scalping','heartbeat_at'),('caucion','heartbeat_at')):
                row['paper'][path[0]][path[1]]=beat
            row['paper']['observer_heartbeat_at']=beat
            row['shadow']['idle_heartbeat_at']=beat
        with self.assertRaisesRegex(ValueError,'COMPOSITE_SESSION_DAY_OR_IDLE_WINDOW_RED'):
            comparison.compose(a,b,c,d,pre,eod,SHA,TREE,DEPLOY)


if __name__=='__main__':unittest.main()
