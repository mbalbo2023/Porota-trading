"""One-shot ROOTLESS_SPLIT COLD recovery: flat PAPER book and hard stop gates."""
import os
from datetime import datetime, timezone, timedelta
import json
import hashlib
import subprocess
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import rc6_rootless_cold_recovery as cold


class FlatPaperTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Path(self.folder.name) / 'observer_v17.db'
        with sqlite3.connect(self.db) as connection:
            connection.executescript('''
                CREATE TABLE observer_state(id INTEGER PRIMARY KEY, mode TEXT, real_orders_sent INTEGER,
                  process_state TEXT, session_state TEXT, heartbeat_at TEXT, ppi_auth TEXT, detail TEXT,
                  last_market_data_at TEXT);
                INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',0,'RUNNING','MARKET_OPEN',
                   '2026-11-16T14:00:00+00:00','OK','','2026-11-16T14:00:00+00:00');
                CREATE TABLE paper_workspace(id INTEGER PRIMARY KEY,dataset_id TEXT);
                INSERT INTO paper_workspace VALUES(1,'session-01');
                CREATE TABLE paper_positions(status TEXT);
                CREATE TABLE paper_cauciones(status TEXT);
                CREATE TABLE paper_future_positions(status TEXT);
                CREATE TABLE paper_family_lifecycle(family TEXT,state TEXT);
                CREATE TABLE paper_exit_intents(paper_id TEXT, state TEXT);
                CREATE TABLE paper_future_exit_intents(lifecycle_id TEXT, state TEXT);
            ''')
        with sqlite3.connect(self.db) as connection:
            connection.execute('UPDATE observer_state SET heartbeat_at=?',
                (datetime.now(timezone.utc).isoformat(),))

    def test_open_market_itself_is_not_a_veto_when_book_is_flat(self):
        observed = cold.flat_book(self.db)
        self.assertEqual(observed['state'], 'PAPER_FLAT')
        self.assertEqual(observed['real_orders_sent'], 0)
        self.assertEqual(observed['session'], 'MARKET_OPEN')

    def test_ppi_only_degraded_boot_sessions_use_existing_runtime_classifier(self):
        scenarios=(('BOOT_CALENDAR','COOLDOWN','PPI en espera de reintento;'),
                   ('BOOT_PPI_LOGIN','ERROR','Login fallo:'),
                   ('CHECKING','COOLDOWN','Login local bloqueado por interlock'))
        for session,auth,detail in scenarios:
            with self.subTest(session=session,auth=auth):
                with sqlite3.connect(self.db) as connection:
                    connection.execute('UPDATE observer_state SET process_state=?,session_state=?,ppi_auth=?,detail=?',
                                       ('DEGRADED',session,auth,detail+' test privado'))
                self.assertEqual(cold.flat_book(self.db)['state'],'PAPER_FLAT')

    def test_ppi_cooldown_does_not_admit_unknown_sessions_causes_or_auth_mismatch(self):
        for process,session,auth,detail in (
            ('DEGRADED','UNKNOWN','COOLDOWN','Login fallo: privado'),
            ('DEGRADED','BOOT_CALENDAR','NOT_ATTEMPTED','Login fallo: privado'),
            ('DEGRADED','BOOT_CALENDAR','COOLDOWN','CAUCION_LEDGER_INVALID'),
            ('DEGRADED','BOOT_CALENDAR','OK','PPI en espera de reintento;'),
            ('RUNNING','MARKET_OPEN','COOLDOWN','PPI en espera de reintento;'),
        ):
            with self.subTest(process=process,session=session,auth=auth):
                with sqlite3.connect(self.db) as connection:
                    connection.execute('UPDATE observer_state SET process_state=?,session_state=?,ppi_auth=?,detail=?',
                                       (process,session,auth,detail))
                with self.assertRaisesRegex(cold.ColdHold,'PAPER_PROCESS_NOT_QUIESCIBLE'):
                    cold.flat_book(self.db)

    def test_poststop_bootcalendar_requires_exact_clean_ack_and_new_heartbeat(self):
        since=datetime.now(timezone.utc)-timedelta(seconds=30)
        with sqlite3.connect(self.db) as connection:
            connection.execute('UPDATE observer_state SET process_state=?,session_state=?,ppi_auth=?,detail=?,heartbeat_at=?',
                               ('STOPPED','BOOT_CALENDAR','COOLDOWN',
                                'Observador detenido ordenadamente.',datetime.now(timezone.utc).isoformat()))
        self.assertEqual(cold.flat_book(self.db,stopped=True,clean_exit=True,stopped_after=since)['state'],
                         'PAPER_FLAT')
        with self.assertRaisesRegex(cold.ColdHold,'PAPER_STOP_ACK_UNVERIFIED'):
            cold.flat_book(self.db,stopped=True,clean_exit=True,stopped_after=datetime.now(timezone.utc)+timedelta(seconds=5))
        with sqlite3.connect(self.db) as connection:
            connection.execute('UPDATE observer_state SET detail=?',('No fue una parada limpia',))
        with self.assertRaisesRegex(cold.ColdHold,'PAPER_STOP_ACK_UNVERIFIED'):
            cold.flat_book(self.db,stopped=True,clean_exit=True,stopped_after=since)

    def test_open_paper_positions_cauciones_futures_and_fund_obligations_are_stoppers(self):
        for table, status in (('paper_positions', 'OPEN'), ('paper_cauciones', 'OPEN'),
                              ('paper_future_positions', 'ACTIVE'),
                              ('paper_family_lifecycle', 'PENDING')):
            with self.subTest(table=table):
                with sqlite3.connect(self.db) as connection:
                    if table == 'paper_family_lifecycle':
                        connection.execute('INSERT INTO paper_family_lifecycle VALUES (?,?)', ('FCI', status))
                    else:
                        connection.execute(f'INSERT INTO {table} VALUES (?)', (status,))
                with self.assertRaisesRegex(cold.ColdHold, 'PAPER_EXPOSURE_NOT_FLAT'):
                    cold.flat_book(self.db)
                with sqlite3.connect(self.db) as connection:
                    connection.execute('DELETE FROM ' + table)

    def test_missing_table_and_nonzero_or_boolean_real_orders_fail_closed(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('DROP TABLE paper_future_positions')
        with self.assertRaises(cold.ColdHold):
            cold.flat_book(self.db)

    def test_pending_exit_not_open_still_blocks_flat_claim(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT INTO paper_positions VALUES ('EXIT_PENDING')")
        with self.assertRaisesRegex(cold.ColdHold, 'PAPER_EXPOSURE_NOT_FLAT'):
            cold.flat_book(self.db)

    def test_null_status_cannot_be_mistaken_for_a_terminal_book(self):
        for table in ('paper_positions','paper_cauciones','paper_future_positions'):
            with self.subTest(table=table):
                with sqlite3.connect(self.db) as connection:
                    connection.execute('INSERT INTO '+table+' VALUES (NULL)')
                with self.assertRaisesRegex(cold.ColdHold, 'PAPER_EXPOSURE_NOT_FLAT'):
                    cold.flat_book(self.db)
                with sqlite3.connect(self.db) as connection:
                    connection.execute('DELETE FROM '+table)
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT INTO paper_family_lifecycle VALUES ('FCI',NULL)")
        with self.assertRaisesRegex(cold.ColdHold, 'PAPER_EXPOSURE_NOT_FLAT'):
            cold.flat_book(self.db)
        with sqlite3.connect(self.db) as connection:
            connection.execute('DELETE FROM paper_family_lifecycle')
            connection.execute("INSERT INTO paper_family_lifecycle VALUES (NULL,'REDEEMED')")
        with self.assertRaisesRegex(cold.ColdHold, 'PAPER_EXPOSURE_NOT_FLAT'):
            cold.flat_book(self.db)

    def test_stale_heartbeat_or_unclassified_process_blocks_intraday_stop(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET heartbeat_at='2026-11-01T00:00:00+00:00'")
        with self.assertRaisesRegex(cold.ColdHold, 'PAPER_HEARTBEAT_UNVERIFIED'):
            cold.flat_book(self.db)
        with sqlite3.connect(self.db) as connection:
            connection.execute('UPDATE observer_state SET heartbeat_at=?, process_state=?',
                (datetime.now(timezone.utc).isoformat(), 'STARTING'))
        with self.assertRaisesRegex(cold.ColdHold, 'PAPER_PROCESS_NOT_QUIESCIBLE'):
            cold.flat_book(self.db)

    def test_stopped_market_open_requires_clean_exit_proof(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE observer_state SET process_state='STOPPED',session_state='OPEN',detail='Observador detenido ordenadamente.'")
        with self.assertRaises(cold.ColdHold):
            cold.flat_book(self.db)
        self.assertEqual(cold.flat_book(self.db, stopped=True, clean_exit=True)['state'], 'PAPER_FLAT')

    def test_unreconciled_paper_exit_intent_blocks_archive_even_if_positions_look_closed(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT INTO paper_exit_intents VALUES ('paper-001','EXIT_PENDING_MARKET_CLOSED')")
        with self.assertRaisesRegex(cold.ColdHold,'PAPER_INFLIGHT_INTENT_PENDING'):
            cold.flat_book(self.db)

    def test_future_intent_and_margin_liability_block_flat_claim(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT INTO paper_future_exit_intents VALUES ('future-001','EXIT_PENDING_EXECUTION')")
        with self.assertRaisesRegex(cold.ColdHold,'PAPER_INFLIGHT_INTENT_PENDING'):
            cold.flat_book(self.db)
        with sqlite3.connect(self.db) as connection:
            connection.execute('DELETE FROM paper_future_exit_intents')
            connection.execute("INSERT INTO paper_family_lifecycle VALUES ('FUTUROS','MARGIN_DEFICIT')")
        with self.assertRaisesRegex(cold.ColdHold,'PAPER_EXPOSURE_NOT_FLAT'):
            cold.flat_book(self.db)


class RootlessAdmissionTests(unittest.TestCase):
    def test_open_market_suspended_flat_quiescence_accepted_without_rootful_aliases(self):
        cid = {'observer': 'a'*64, 'dashboard': 'b'*64}
        state = {'backend': 'ROOTLESS_SPLIT', 'owned_ids': cid, 'owned_stopped': True,
                 'lock_held': True, 'mode': 'PRODUCTION_PAPER', 'real_routes': 'NOT_CALLED',
                 'order_routing_blocked': True, 'inflight_absent': True,
                 'critical_healthy': True, 'ppi_watch_accessed': False,
                 'boca_touched': False, 'real_orders_sent': 0, 'book_flat': True}
        with patch.object(cold, 'resource_reserve'):
            cold.typed_admit(Path('/opt/porota-trading'), lambda: state, cid)

    def test_all_safety_gates_rechecked_at_every_boundary(self):
        cid = {'observer': 'a'*64, 'dashboard': 'b'*64}
        state = {'backend': 'ROOTLESS_SPLIT', 'owned_ids': cid, 'owned_stopped': True,
                 'lock_held': True, 'mode': 'PRODUCTION_PAPER', 'real_routes': 'NOT_CALLED',
                 'order_routing_blocked': True, 'inflight_absent': True,
                 'critical_healthy': True, 'ppi_watch_accessed': False,
                 'boca_touched': False, 'real_orders_sent': 0, 'book_flat': True}
        for bad in ({'owned_stopped': False}, {'lock_held': False},
                    {'mode': 'LIVE'}, {'real_orders_sent': 1},
                    {'book_flat': False}, {'inflight_absent': False},
                    {'ppi_watch_accessed': True}, {'backend': 'ROOTFUL'},
                    {'owned_ids': {'observer': 'c'*64, 'dashboard': 'b'*64}}):
            with self.subTest(bad=bad), patch.object(cold, 'resource_reserve'):
                with self.assertRaises(cold.ColdHold):
                    cold.typed_admit(Path('/opt/porota-trading'), lambda: {**state, **bad}, cid)

    def test_bridge_runs_existing_successor_quarantine_and_restores_legacy_admission(self):
        legacy = Mock()
        original = Mock()
        legacy._admit = original
        successor = Mock()
        successor.old = legacy
        successor.quarantine.side_effect = lambda root, *, admission, expected_owned_ids, checkpoint: (
            legacy._admit(root, admission, expected_owned_ids), {'state': 'QUARANTINED_FAILED'})[1]
        cid = {'observer': 'a'*64, 'dashboard': 'b'*64}
        state = {'backend': 'ROOTLESS_SPLIT', 'owned_ids': cid, 'owned_stopped': True,
                 'lock_held': True, 'mode': 'PRODUCTION_PAPER', 'real_routes': 'NOT_CALLED',
                 'order_routing_blocked': True, 'inflight_absent': True,
                 'critical_healthy': True, 'ppi_watch_accessed': False,
                 'boca_touched': False, 'real_orders_sent': 0, 'book_flat': True}
        with patch.object(cold, 'resource_reserve'):
            result = cold.quarantine_existing(successor, Path('/opt/porota-trading'), lambda: state, cid)
        self.assertEqual(result['state'], 'QUARANTINED_FAILED')
        self.assertIs(legacy._admit, original)

    def test_uncertain_stop_outcome_restores_both_existing_cids_without_recreate(self):
        from datetime import datetime, timezone
        current = {'roles':{'observer':'a'*64,'dashboard':'b'*64}}
        host = Mock()
        host.inspect.side_effect = lambda cid: {'Id':cid, 'State':{'Running':False}}
        host.docker.call.return_value = ''
        host.postflight.return_value = {'dashboard':'healthy', 'observer':'running'}
        result = cold.ensure_pair_restored(host, current, datetime.now(timezone.utc))
        self.assertEqual(result['state'], 'RESTORED_ORIGINAL_RUNTIME')
        self.assertEqual(host.docker.call.call_count,2)
        self.assertEqual({call.args[-1] for call in host.docker.call.call_args_list},
                         {'a'*64,'b'*64})
        self.assertEqual(host.postflight.call_count,1)

    def test_poststop_ack_precedes_observer_restart_and_is_pending_not_degraded(self):
        from scripts import porota_rootless_host as frozen_host
        current = {'roles':{'observer':'a'*64,'dashboard':'b'*64}}
        host = Mock()
        host.inspect.side_effect = lambda cid: {'Id':cid, 'State':{'Running':False}}
        stopped_heartbeat = datetime.now(timezone.utc)-timedelta(seconds=2)
        started_before_stop = stopped_heartbeat-timedelta(seconds=30)
        stopped_row = {'mode':'PRODUCTION_PAPER','real_orders_sent':0,
                       'process_state':'STOPPED','session_state':'BOOT_CALENDAR',
                       'heartbeat_at':stopped_heartbeat.isoformat(),
                       'ppi_auth':'COOLDOWN','detail':cold.STOP_ACK}
        with self.assertRaisesRegex(frozen_host.Hold,'RUNTIME_UNCLASSIFIED_DEGRADED'):
            frozen_host.classify_runtime(stopped_row,now=datetime.now(timezone.utc),
                                         started=started_before_stop)

        def postflight(_current, birth_floor, *, budget):
            self.assertEqual(budget,600)
            self.assertGreater(birth_floor,stopped_heartbeat)
            self.assertLessEqual(birth_floor,datetime.now(timezone.utc))
            self.assertEqual(frozen_host.classify_runtime(stopped_row,
                now=datetime.now(timezone.utc),started=birth_floor)['status'],'PENDING')
            restarted_row={**stopped_row,'process_state':'DEGRADED',
                           'detail':'PPI en espera de reintento; prueba',
                           'heartbeat_at':(birth_floor+timedelta(seconds=1)).isoformat()}
            self.assertEqual(frozen_host.classify_runtime(restarted_row,
                now=birth_floor+timedelta(seconds=2),started=birth_floor),
                {'status':'READY','source_alert':'AMBER_PPI_LOGIN_UNAVAILABLE'})
            self.assertEqual(host.docker.call.call_count,2)
            self.assertEqual(host.docker.call.call_args.args[-1],current['roles']['observer'])
            return {'dashboard':'healthy','observer':'running'}

        host.postflight.side_effect = postflight
        self.assertEqual(cold.ensure_pair_restored(host,current,started_before_stop)['state'],
                         'RESTORED_ORIGINAL_RUNTIME')
        self.assertEqual(host.postflight.call_count,1)

    def test_postflight_fatal_error_never_retries_or_recreates(self):
        from scripts import porota_rootless_host as frozen_host
        current={'roles':{'observer':'a'*64,'dashboard':'b'*64}}
        host=Mock()
        host.inspect.side_effect=lambda cid:{'Id':cid,'State':{'Running':False}}
        host.postflight.side_effect=frozen_host.Hold('PAPER_RUNTIME_SAFETY')
        with self.assertRaisesRegex(cold.ColdHold,'PAPER_RUNTIME_SAFETY'):
            cold.ensure_pair_restored(host,current,datetime.now(timezone.utc))
        host.postflight.assert_called_once()
        self.assertEqual(host.docker.call.call_count,2)

    def test_observer_stops_between_inspects_uses_fresh_floor_before_restart(self):
        current={'roles':{'observer':'a'*64,'dashboard':'b'*64}}
        host=Mock()
        observer_inspects=iter([True,False])
        def inspect(cid):
            running=next(observer_inspects) if cid == current['roles']['observer'] else True
            return {'Id':cid,'State':{'Running':running}}
        host.inspect.side_effect=inspect
        started=datetime.now(timezone.utc)-timedelta(seconds=120)
        cold.ensure_pair_restored(host,current,started)
        host.docker.call.assert_called_once_with('container','start',current['roles']['observer'],timeout=95)
        self.assertGreater(host.postflight.call_args.args[1],started+timedelta(seconds=90))

    def test_distinct_primary_and_restoration_causes_survive_failed_cold_receipt(self):
        from contextlib import nullcontext
        from scripts import porota_rootless_host as host_module
        import sys
        successor=Mock()
        host=Mock()
        host.identity.state=Path('/nonexistent/test-identity-state')
        host.identity.operator=Path('/nonexistent/test-operator')
        current={'source_sha':cold.FROZEN_SOURCE_SHA,'source_tree':cold.FROZEN_TREE,
                 'roles':dict(cold.FROZEN_CIDS)}
        release={'source_names':[],'queue_failed':True,'archive_complete':False,
                 'failure_code':'SHADOW_COLD_DISK_RESERVE_BREACHED'}
        book={'state':'PAPER_FLAT','session':'BOOT_CALENDAR','identity':[1,2]}
        with patch.dict(sys.modules,{'scripts.rc6_cold2_successor':successor}), \
             patch.object(host_module,'Host',return_value=host), \
             patch.object(host_module,'user_lock',return_value=nullcontext()), \
             patch.object(successor,'inspect',return_value=release), \
             patch.object(cold,'frozen_release',return_value=(current,{})), \
             patch.object(cold,'controller_live',return_value={'children':['r']*8}), \
             patch.object(cold,'flat_book',return_value=book), \
             patch.object(cold,'operator_custody',return_value={}), \
             patch.object(cold,'resource_reserve',return_value={'free_bytes':35*1024**3,'free_inodes':200}), \
             patch.object(cold,'restart_policy_witness',return_value={'observer':['always',0]}), \
             patch.object(cold,'operator_state_witness',return_value='pause-fingerprint'), \
             patch.object(cold,'critical_broker_proof',return_value={'state':'HEALTHY_ISSUES_ONLY'}), \
             patch.object(cold,'legacy_writers_absent',return_value={'state':'NO_LEGACY_WRITERS'}), \
             patch.object(cold,'arm_stop_witness',return_value='nonce'), \
             patch.object(cold,'stopped_admission',side_effect=cold.ColdHold('PAPER_STOP_ACK_UNVERIFIED')), \
             patch.object(cold,'ensure_pair_restored',side_effect=cold.ColdHold('RUNTIME_POSTFLIGHT_UNVERIFIED')):
            result=cold.operate('cold_recover',Path('/opt/porota-trading'))
        self.assertEqual(result['state'],'BLOQUEADO')
        self.assertEqual(result['recovery_failure_signature'],'PAPER_STOP_ACK_UNVERIFIED')
        self.assertEqual(result['recovery_failure_stage'],'POSTSTOP_ADMISSION')
        self.assertEqual(result['restoration_failure_signature'],'RUNTIME_POSTFLIGHT_UNVERIFIED')
        self.assertFalse(result['runtime_validated'])

    def test_idempotent_resume_accepts_existing_running_observer_with_fresh_health(self):
        from datetime import datetime, timezone
        current = {'roles':{'observer':'a'*64,'dashboard':'b'*64}}
        host=Mock()
        host.inspect.side_effect=lambda cid:{'Id':cid,'State':{'Running':True}}
        now=datetime.now(timezone.utc)
        cold.ensure_pair_restored(host,current,now)
        host.docker.call.assert_not_called()
        self.assertLess(host.postflight.call_args.args[1],now)

    def test_recovery_guard_does_not_count_rootless_container_alive_as_quiescent(self):
        cid = {'observer':'a'*64,'dashboard':'b'*64}
        with patch.object(cold,'resource_reserve'), self.assertRaises(cold.ColdHold):
            cold.typed_admit(Path('/opt/porota-trading'),lambda:{'backend':'ROOTLESS_SPLIT',
                'owned_ids':cid,'owned_stopped':False},cid)

    def test_critical_broker_must_prove_healthy_issues_only_capability(self):
        inspected = {'Id':'a'*64,'Name':'/porota_critical_approval_rc6',
                     'Image':'sha256:'+'f'*64,
                     'RestartCount':0,'State':{'Status':'running', 'OOMKilled':False,
                        'Health':{'Status':'healthy'}}}
        evidence = [subprocess.CompletedProcess([],0,json.dumps([inspected]),''),
                    subprocess.CompletedProcess([],0,'RC6_CRITICAL_ISSUES_ONLY=GREEN\n','')]
        with patch.object(cold.subprocess,'run',side_effect=evidence) as run:
            result = cold.critical_broker_proof()
        self.assertEqual(result['state'],'HEALTHY_ISSUES_ONLY')
        self.assertEqual(run.call_count,2)
        self.assertTrue(all('porota_critical_approval_rc6' in ' '.join(call.args[0])
                            for call in run.call_args_list))

    def test_legacy_rootful_paper_writers_must_be_absent_before_cold_mutation(self):
        other=subprocess.CompletedProcess([],0,'ppi-watch\nboca-socios-controller-1\n','')
        with patch.object(cold.subprocess,'run',return_value=other) as run:
            self.assertEqual(cold.legacy_writers_absent()['state'],'NO_LEGACY_WRITERS')
        self.assertEqual(run.call_count,1)
        self.assertNotIn('--filter',run.call_args.args[0])
        active=subprocess.CompletedProcess([],0,'ppi-watch\nporota_production_observer\n','')
        with patch.object(cold.subprocess,'run',return_value=active):
            with self.assertRaises(cold.ColdHold):
                cold.legacy_writers_absent()
        prefixed=subprocess.CompletedProcess([],0,'/porota_production_dashboard\n','')
        with patch.object(cold.subprocess,'run',return_value=prefixed):
            with self.assertRaisesRegex(cold.ColdHold,'LEGACY_PAPER_WRITER_ACTIVE'):
                cold.legacy_writers_absent()

    def test_stop_witness_is_required_for_resume_and_cannot_reuse_stale_or_changed_operator(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)
            cids={'observer':'a'*64,'dashboard':'b'*64}
            with self.assertRaisesRegex(cold.ColdHold,'COLD_STOP_WITNESS_REQUIRED'):
                cold.verify_stop_witness(state,cids,'operator-sha')
            token=cold.arm_stop_witness(state,cids,'operator-sha')
            self.assertEqual(cold.verify_stop_witness(state,cids,'operator-sha'),token)
            with self.assertRaisesRegex(cold.ColdHold,'COLD_STOP_WITNESS_MISMATCH'):
                cold.verify_stop_witness(state,cids,'other-operator')
            with self.assertRaisesRegex(cold.ColdHold,'COLD_STOP_WITNESS_EXISTS'):
                cold.arm_stop_witness(state,cids,'operator-sha')
            with patch('time.time',return_value=10**10):
                with self.assertRaisesRegex(cold.ColdHold,'COLD_STOP_WITNESS_STALE'):
                    cold.verify_stop_witness(state,cids,'operator-sha')
            cold.clear_stop_witness(state,token)
            with self.assertRaisesRegex(cold.ColdHold,'COLD_STOP_WITNESS_REQUIRED'):
                cold.verify_stop_witness(state,cids,'operator-sha')

    def test_manual_cold_resume_does_not_restart_operator_paused_pair_without_incident(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as d:
            source=Mock()
            source.user_lock.return_value=nullcontext()
            current={'source_sha':cold.FROZEN_SOURCE_SHA,'source_tree':cold.FROZEN_TREE,
                     'roles':dict(cold.FROZEN_CIDS)}
            source.read_release_state.return_value=(current,{'source_sha':cold.FROZEN_SOURCE_SHA})
            host=Mock()
            host.identity.state=Path(d)
            host.identity.operator=Path(d)
            host.check_role.return_value={'HostConfig':{'RestartPolicy':{
                'Name':'always','MaximumRetryCount':0}}}
            with patch.object(cold,'operator_state_witness',return_value='pause'):
                with self.assertRaisesRegex(cold.ColdHold,'COLD_STOP_WITNESS_REQUIRED'):
                    cold.resume_only(source,host)
            host.docker.call.assert_not_called()

    def test_stopped_admission_rejects_live_pid_or_drifted_shadow_routes(self):
        cid={'observer':'a'*64,'dashboard':'b'*64}
        item={'State':{'Running':False,'OOMKilled':False,'ExitCode':0,'Pid':0,'Status':'exited'}}
        host=Mock()
        host.root=Path('/opt/porota-trading')
        host.check_role.return_value=item
        host.check_inventory.return_value=None
        host.boundary.return_value='daemon'
        source=Mock()
        source.DB_RELATIVE=Path('data/paper_v17/observer_v17.db')
        paper={'state':'PAPER_FLAT','mode':'PRODUCTION_PAPER',
               'real_orders_sent':0,'identity':[10,20]}
        with patch.object(cold,'flat_book',return_value=paper), \
             patch.object(cold,'critical_broker_proof',return_value={'state':'HEALTHY_ISSUES_ONLY'}), \
             patch.object(cold,'read_shadow_status',return_value={'real_routes':'NOT_CALLED',
                'real_orders_sent':0}) as shadow, \
             patch.object(cold,'operator_custody',return_value={'operator_uid_verified':True}), \
             patch.object(cold,'legacy_writers_absent',return_value={'state':'NO_LEGACY_WRITERS'}):
            self.assertTrue(cold.stopped_admission(source,host,{'roles':cid},True)['inflight_absent'])
            item['State']['Pid']=44
            with self.assertRaises(cold.ColdHold):
                cold.stopped_admission(source,host,{'roles':cid},True)
            item['State']['Pid']=0
            shadow.return_value={'real_routes':'CALLED','real_orders_sent':0}
            with self.assertRaises(cold.ColdHold):
                cold.stopped_admission(source,host,{'roles':cid},True)

    def test_operator_pause_config_fingerprint_detects_drift_without_reading_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            marker=folder/'pause.json'
            marker.write_text('paused')
            first=cold.operator_state_witness(folder)
            marker.write_text('resume')
            second=cold.operator_state_witness(folder)
            self.assertNotEqual(first,second)
            (folder/'alias').symlink_to(marker)
            with self.assertRaises(cold.ColdHold):
                cold.operator_state_witness(folder)


if __name__ == '__main__':
    unittest.main()
