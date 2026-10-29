"""Adversarial local tests for the A175 failed-Deploy read-only diagnostic."""
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/'scripts/rc6_a175_pending_runtime_ro.py'
spec=importlib.util.spec_from_file_location('a175_pending_ro',PATH)
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
NOW=datetime(2026,10,29,1,30,tzinfo=timezone.utc)


class PendingTests(unittest.TestCase):
    def test_frozen_manifest_bytes_are_pinned_no_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'frozen.json';raw=b'{"image_id":"loaded"}\n'
            path.write_bytes(raw)
            with patch.object(probe,'FROZEN',path), \
                 patch.object(probe,'FROZEN_SHA256',hashlib.sha256(raw).hexdigest()):
                self.assertEqual(probe.frozen_manifest_hash()['bytes'],len(raw))
                path.write_bytes(raw+b' ')
                with self.assertRaisesRegex(ValueError,'FROZEN_MANIFEST_HASH_DRIFT'):
                    probe.frozen_manifest_hash()
                path.unlink();path.symlink_to(Path(directory)/'target')
                with self.assertRaises(OSError):probe.frozen_manifest_hash()

    def marker(self):
        return {'candidate_sha':probe.CANDIDATE,'deploy_sha':probe.DEPLOY_SHA,
                'image_id':probe.IMAGE,'validation_status':'DEPLOYED_VALIDATION_PENDING',
                'mode':'PRODUCTION_PAPER','real_orders_sent':0,
                'real_order_routes':'NOT_CALLED','ppi_watch_untouched':True,
                'recorded_at':NOW.isoformat()}

    def test_marker_pending_only_and_bool_counter_rejected(self):
        with patch.object(probe,'safe_json',return_value=self.marker()):
            self.assertEqual(probe.marker(NOW)['validation'],
                             'DEPLOYED_VALIDATION_PENDING')
        for update in ({'validation_status':'VALIDATED_RUNTIME'},
                       {'real_orders_sent':False},{'image_id':'sha256:'+'0'*64},
                       {'deploy_sha':'0'*40}):
            data={**self.marker(),**update}
            with patch.object(probe,'safe_json',return_value=data):
                with self.assertRaisesRegex(ValueError,'PENDING_MARKER_SAFETY_OR_IDENTITY_RED'):
                    probe.marker(NOW)

    def test_older_marker_cannot_be_reused(self):
        data=self.marker();data['recorded_at']='2026-10-28T23:30:00+00:00'
        with patch.object(probe,'safe_json',return_value=data):
            with self.assertRaisesRegex(ValueError,'PENDING_MARKER_OLDER_THAN_DEPLOY'):
                probe.marker(NOW)

    def test_systemctl_allowlist_excludes_mutations(self):
        for argv in (['systemctl','start','porota-preopen-rc6.service'],
                     ['systemctl','reset-failed','porota-preopen-rc6.service'],
                     ['systemctl','show','ppi-watch.service','--property=ActiveState']):
            with self.assertRaisesRegex(ValueError,'MUTATING_COMMAND_FORBIDDEN'):
                probe.command(argv)

    def test_database_flat_paper_and_workers_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path=Path(directory)/'observer.db'
            with sqlite3.connect(db_path) as db:
                db.executescript('''
                  CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,
                    ppi_auth TEXT,process_state TEXT,session_state TEXT,heartbeat_at TEXT);
                  CREATE TABLE paper_positions(status TEXT);
                  CREATE TABLE paper_cauciones(status TEXT);
                  CREATE TABLE paper_future_positions(status TEXT);
                  CREATE TABLE intraday_scalping_worker_state(id INTEGER,state TEXT,
                    heartbeat_at TEXT,real_orders_sent INTEGER,failed INTEGER);
                  CREATE TABLE paper_caucion_cash_sweep_state(id INTEGER,state TEXT,
                    heartbeat_at TEXT,real_orders_sent INTEGER,routes_json TEXT);
                  CREATE TABLE paper_supervisor_state(id INTEGER,state TEXT,heartbeat_at TEXT);
                  CREATE TABLE paper_exit_reader_state(id INTEGER,state TEXT,heartbeat_at TEXT);
                ''')
                stamp=NOW.isoformat()
                db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'OK',"
                           "'WAITING_MARKET','MARKET_CLOSED',?)",(stamp,))
                db.execute("INSERT INTO intraday_scalping_worker_state VALUES(1,'WAITING_MARKET',?,0,0)",(stamp,))
                db.execute("INSERT INTO paper_caucion_cash_sweep_state VALUES(1,'WAITING_WINDOW',?,0,'[]')",(stamp,))
                db.execute("INSERT INTO paper_supervisor_state VALUES(1,'RUNNING',?)",(stamp,))
                db.execute("INSERT INTO paper_exit_reader_state VALUES(1,'WAITING_MARKET',?)",(stamp,))
            with patch.object(probe,'DB',db_path):
                before=db_path.stat().st_mtime_ns
                result=probe.db_snapshot(NOW)
                self.assertTrue(result['three_books_flat'])
                self.assertEqual(result['process_state'],'WAITING_MARKET')
                self.assertNotIn('routes_json',json.dumps(result))
                self.assertEqual(before,db_path.stat().st_mtime_ns)
                with sqlite3.connect(db_path) as db:
                    db.execute("INSERT INTO paper_cauciones VALUES('OPEN')")
                with self.assertRaisesRegex(ValueError,'PAPER_BOOK_NOT_FLAT'):
                    probe.db_snapshot(NOW)
                with sqlite3.connect(db_path) as db:
                    db.execute('DELETE FROM paper_cauciones')
                    db.execute("UPDATE paper_caucion_cash_sweep_state SET routes_json='[\"real\"]'")
                with self.assertRaisesRegex(ValueError,'CAUCION_REAL_ROUTE_RED'):
                    probe.db_snapshot(NOW)

    def test_runtime_child_source_is_exact(self):
        children={name:{'state':'RUNNING','pid':123,'restarts':0,'last_error':None}
                  for name in probe.ROLES}
        health={'schema':'rc6.runtime-child-health.v1','source_sha':probe.CANDIDATE,
                'candidate_tree_sha':probe.TREE,'recorded_at':NOW.isoformat(),
                'children':children}
        with patch.object(probe,'safe_json',return_value=health):
            self.assertEqual(len(probe.runtime_children(NOW)['children']),8)
        health['source_sha']='0'*40
        with patch.object(probe,'safe_json',return_value=health):
            with self.assertRaisesRegex(ValueError,'CHILD_HEALTH_SOURCE_RED'):
                probe.runtime_children(NOW)

    def test_iol_stored_live_label_with_old_timestamps_is_amber(self):
        value={'cache_state':'LIVE_FRESH',
               'continuation_state':'CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL',
               'decision_effect':'OBSERVE_ONLY','real_money_authorized':False,
               'fallback_order':['IOL_LIVE_BOUNDED_RETRY','IOL_LAST_KNOWN_GOOD',
                                 'PPI_PRIMARY','BYMA_PUBLIC_COMPLEMENTARY'],
               'last_known_good_at':(NOW-timedelta(hours=18)).isoformat(),
               'refreshed_at':(NOW-timedelta(hours=18)).isoformat()}
        with patch.object(probe,'safe_json',return_value=value):
            result=probe.iol_cache(NOW)
        self.assertEqual(result['effective_state'],'AMBER_STALE_OR_UNAVAILABLE')
        self.assertFalse(result['open_freshness_attested'])
        value['real_money_authorized']=True
        with patch.object(probe,'safe_json',return_value=value):
            with self.assertRaisesRegex(ValueError,'IOL_CACHE_FAILSAFE_CONTRACT_RED'):
                probe.iol_cache(NOW)

    def test_wrong_droplet_rejects_before_mutex_and_runtime_reads(self):
        out=io.StringIO()
        with patch.object(probe,'metadata',side_effect=['594077618','nyc1']), \
             patch.object(probe,'read_lock',side_effect=AssertionError('MUTEX_TOUCHED')), \
             redirect_stdout(out):
            rc=probe.pulse()
        receipt=json.loads(out.getvalue().split('=',1)[1])
        self.assertEqual(rc,2)
        self.assertEqual(receipt['error_code'],'DROPLET_IDENTITY_RED')
        self.assertNotIn('marker',receipt)

    def test_pending_receipt_never_claims_go_when_runtime_inconsistent(self):
        out=io.StringIO()
        class FD:
            def __int__(self):return 42
        with patch.object(probe,'metadata',side_effect=['594077619','nyc1']), \
             patch.object(probe,'read_lock',return_value=42), \
             patch.object(probe.os,'close'), \
             patch.object(probe,'marker',return_value={'validation':'DEPLOYED_VALIDATION_PENDING'}), \
             patch.object(probe,'frozen_manifest_hash',return_value={'sha256':probe.FROZEN_SHA256}), \
             patch.object(probe,'db_snapshot',return_value={'process_state':'DEGRADED',
                'session_state':'MARKET_CLOSED'}), \
             patch.object(probe,'runtime_children',return_value={'children':{
                'scanner':{'running':True,'pid_present':True,'last_error_present':False}}}), \
             patch.object(probe,'guardian',return_value={'state':'AUTHENTICATED'}), \
             patch.object(probe,'iol_cache',return_value={'effective_state':'AMBER_STALE_OR_UNAVAILABLE'}), \
             patch.object(probe,'dashboard',return_value={'process_state':'WAITING_MARKET',
                'session_state':'MARKET_CLOSED'}), \
             patch.object(probe,'service',return_value={'state':'failed','result':'EXIT_CODE'}), \
             redirect_stdout(out):
            rc=probe.pulse()
        receipt=json.loads(out.getvalue().split('=',1)[1])
        self.assertEqual(rc,0)
        self.assertEqual(receipt['status'],'DIAGNOSTIC_PENDING_NO_GO')
        self.assertFalse(receipt['runtime_consistent'])
        self.assertFalse(receipt['operational_go'])


if __name__=='__main__':unittest.main()
