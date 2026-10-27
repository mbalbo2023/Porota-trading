import base64
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import rc6_deploy_scheduler_suspend as suspend
from scripts import rc6_deploy_quiescence as quiescence


class SuspendTests(unittest.TestCase):
    def test_open_hold_requires_exact_stopped_paper_receipt(self):
        originals = {name: {'State': {'Status': 'exited'}} for name in quiescence.OWNED}
        receipt = {'status': 'DRAINED', 'real_orders_sent': 0,
                   'ppi_watch_accessed': False, 'market_open_suspended': True,
                   'open_paper_carryover': False, 'spot_carryover': None,
                   'caucion_carryover': None, 'paused_postclose': False,
                   'intraday_caucion': False}
        with patch.object(quiescence, 'inspect', side_effect=lambda n: originals[n]), \
             patch.object(quiescence, 'adopt_stopped_after_failed_install', return_value=receipt) as adopt, \
             patch.object(quiescence, 'exact_clean_stopped') as exact, \
             patch.object(quiescence, 'admit', return_value='RUNNING') as admit, \
             patch.object(quiescence, 'critical_admission'):
            suspend.require_exact_stopped_open_flat('a'*40)
            self.assertTrue(adopt.call_args.kwargs['allow_market_open_suspend'])
            exact.assert_called_once_with(receipt)
            self.assertTrue(admit.call_args.kwargs['stopped'])
            receipt['real_orders_sent'] = 1
            with self.assertRaisesRegex(ValueError, 'SCHEDULER_STOPPED_OPEN_FLAT_RED'):
                suspend.require_exact_stopped_open_flat('a'*40)
            originals[quiescence.OWNED[0]]['State']['Status'] = 'running'
            with self.assertRaisesRegex(ValueError, 'SCHEDULER_STOPPED_OPEN_IDENTITY_RED'):
                suspend.require_exact_stopped_open_flat('a'*40)

    def test_postinstall_open_flat_hold_uses_own_exact_pending_receipt(self):
        candidate = 'f'*40
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/'data/deploy'
            target.mkdir(parents=True)
            originals = {name: {'Id': str(index)*64, 'Image': 'sha256:'+'a'*64,
                                'State': {'Status': 'exited'}}
                         for index, name in enumerate(quiescence.OWNED, 1)}
            row = {'candidate_sha': candidate, 'status': 'FIX_FORWARD_VALIDATION_REQUIRED',
                   'installation_started': True,
                   'inflight': 'ABSENT_OWNED_RUNTIME_STOPPED',
                   'containers': {name:{'id': value['Id'], 'image':value['Image']}
                                  for name,value in originals.items()},
                   'real_orders_sent':0, 'ppi_watch_accessed':False,
                   'market_open_suspended':True, 'open_paper_carryover':False,
                   'spot_carryover':None, 'caucion_carryover':None,
                   'paused_postclose':False, 'intraday_caucion':False}
            path = target/('quiescence-'+candidate+'.json')
            path.write_text(json.dumps(row))
            with patch.object(quiescence, 'ROOT', root), \
                 patch.object(quiescence, 'inspect', side_effect=lambda n:originals[n]), \
                 patch.object(quiescence, 'adopt_stopped_after_failed_install') as adopt, \
                 patch.object(quiescence, 'exact_clean_stopped') as exact, \
                 patch.object(quiescence, 'admit', return_value='RUNNING') as admit, \
                 patch.object(quiescence, 'critical_admission') as critical:
                suspend.require_exact_stopped_open_flat(candidate)
                adopt.assert_not_called()
                exact.assert_called_once_with(row)
                critical.assert_called_once()
                self.assertTrue(admit.call_args.kwargs['allow_market_open_suspend'])
                row['containers'][quiescence.OWNED[0]]['id'] = 'e'*64
                path.write_text(json.dumps(row))
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_STOPPED_OPEN_INSTALL_IDENTITY_RED'):
                    suspend.require_exact_stopped_open_flat(candidate)

    def test_postinstall_closed_flat_hold_requires_own_receipt_and_paper_zero(self):
        candidate = 'f'*40
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/'data/deploy'
            target.mkdir(parents=True)
            originals = {name: {'Id': str(index)*64, 'Image': 'sha256:'+'a'*64,
                                'State': {'Status': 'exited'}}
                         for index, name in enumerate(quiescence.OWNED, 1)}
            row = {'candidate_sha': candidate, 'status': 'FIX_FORWARD_VALIDATION_REQUIRED',
                   'installation_started': True, 'inflight': 'ABSENT_OWNED_RUNTIME_STOPPED',
                   'containers': {name: {'id': value['Id'], 'image': value['Image']}
                                  for name, value in originals.items()},
                   'real_orders_sent': 0, 'ppi_watch_accessed': False,
                   'market_open_suspended': False, 'open_paper_carryover': False,
                   'spot_carryover': None, 'caucion_carryover': None,
                   'paused_postclose': False, 'intraday_caucion': False,
                   'pending_caucion': False}
            path = target/('quiescence-'+candidate+'.json')
            path.write_text(json.dumps(row))
            with patch.object(quiescence, 'ROOT', root), \
                 patch.object(quiescence, 'inspect', side_effect=lambda n: originals[n]), \
                 patch.object(quiescence, 'exact_clean_stopped') as exact, \
                 patch.object(quiescence, 'admit', return_value='STOPPED') as admit, \
                 patch.object(quiescence, 'critical_admission') as critical:
                suspend.require_exact_stopped_closed_flat(candidate)
                exact.assert_called_once_with(row)
                critical.assert_called_once()
                self.assertTrue(admit.call_args.kwargs['stopped'])
                self.assertNotIn('allow_market_open_suspend', admit.call_args.kwargs)
                for field, value in [('real_orders_sent', 1),
                                     ('market_open_suspended', True),
                                     ('pending_caucion', True)]:
                    bad = {**row, field: value}
                    path.write_text(json.dumps(bad))
                    with self.assertRaisesRegex(ValueError, 'SCHEDULER_STOPPED_CLOSED_FLAT_RED'):
                        suspend.require_exact_stopped_closed_flat(candidate)
                path.write_text(json.dumps(row))
                row['containers'][quiescence.OWNED[0]]['id'] = 'e'*64
                path.write_text(json.dumps(row))
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_STOPPED_CLOSED_INSTALL_IDENTITY_RED'):
                    suspend.require_exact_stopped_closed_flat(candidate)
                path.unlink()
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_STOPPED_CLOSED_RECEIPT_UNSAFE'):
                    suspend.require_exact_stopped_closed_flat(candidate)

    def test_restore_runtime_gate_checks_containers_and_paper_zero(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            db_path = data/'paper_v17/observer_v17.db'
            db_path.parent.mkdir()
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER)')
                db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
            status = {'porota_production_observer':'exited',
                      'porota_production_dashboard':'running'}
            def docker(*args):
                self.assertEqual(args[:3], ('docker','inspect','-f'))
                return status[args[-1]], 0
            with patch.object(suspend,'ROOT',data/'deploy'), patch.object(suspend,'command',docker):
                with self.assertRaisesRegex(ValueError,'SCHEDULER_OWNED_RUNTIME_STOPPED'):
                    suspend.require_owned_running()
                status['porota_production_observer']='running'
                suspend.require_owned_running()
                with sqlite3.connect(db_path) as db:
                    db.execute('UPDATE observer_state SET real_orders_sent=1')
                with self.assertRaisesRegex(ValueError,'SCHEDULER_PAPER_ZERO_REQUIRED'):
                    suspend.require_owned_running()

    def policy(self):
        raw = json.dumps({'units': {
            'systemd/porota-runtime-evidence-rc6.timer': {
                'role': 'ACTIVE_TIMER', 'active': True},
            'systemd/porota-preopen-rc6.timer': {
                'role': 'ACTIVE_TIMER', 'active': True},
            'systemd/porota-deprecated-rc6.timer': {
                'role': 'RETIRED_TIMER', 'active': False},
        }}).encode()
        return base64.b64encode(raw).decode()

    def test_suspend_restore_mutates_only_exact_active_timers(self):
        names = {'porota-runtime-evidence-rc6.timer', 'porota-preopen-rc6.timer'}
        states = dict.fromkeys(names, 'active')
        mutations = []
        def command(*args):
            if args[:2] == ('systemctl', 'list-unit-files'):
                return ('other-ppi-watch.service enabled\n', 0)
            unit = args[2]
            if args[1] == 'is-enabled': return ('enabled', 0)
            if args[1] == 'is-active':
                if unit == 'other-ppi-watch.service': return ('active', 0)
                return (states.get(unit, 'inactive'), 0)
            if args[1] == 'show':
                return (unit.removesuffix('.timer') + '.service', 0)
            if args[1] in {'stop', 'start'}:
                self.assertIn(unit, names)
                mutations.append((args[1], unit))
                states[unit] = 'inactive' if args[1] == 'stop' else 'active'
                return ('', 0)
            raise AssertionError(args)
        with TemporaryDirectory() as directory:
            with patch.object(suspend, 'ROOT', Path(directory)), patch.object(suspend, 'command', command), \
                 patch.object(suspend, 'require_owned_running') as owned_running:
                result = suspend.run('a'*40, 'suspend', self.policy())
                self.assertEqual(result['status'], 'SUSPENDED')
                self.assertEqual({action for action, _ in mutations}, {'stop'})
                self.assertEqual(suspend.run('a'*40, 'suspend', self.policy())['status'], 'SUSPENDED')
                self.assertEqual(len(mutations), 2)
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_RESTORE_RECEIPT_MISSING'):
                    suspend.run('a'*40, 'verify', self.policy())
                self.assertEqual(len(mutations), 2)
                owned_running.side_effect = ValueError('SCHEDULER_OWNED_RUNTIME_STOPPED')
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_OWNED_RUNTIME_STOPPED'):
                    suspend.run('a'*40, 'restore', self.policy())
                self.assertEqual(len(mutations), 2)
                self.assertEqual(json.loads((Path(directory)/('scheduler-suspend-'+'a'*40+'.json')).read_text())['status'], 'SUSPENDED')
                owned_running.side_effect = None
                result = suspend.run('a'*40, 'restore', self.policy())
                self.assertEqual(result['status'], 'RESTORED')
                self.assertEqual({action for action, _ in mutations}, {'stop', 'start'})
                self.assertEqual(len(mutations), 4)
                verified = suspend.run('a'*40, 'verify', self.policy())
                self.assertEqual(verified['status'], 'RESTORED')
                self.assertEqual(len(mutations), 4)
                states['porota-preopen-rc6.timer'] = 'inactive'
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_NOT_RESTORED'):
                    suspend.run('a'*40, 'verify', self.policy())

    def test_adopt_latest_paused_candidate_without_restarting_timer(self):
        names = {'porota-runtime-evidence-rc6.timer', 'porota-preopen-rc6.timer'}
        states = dict.fromkeys(names, 'active')
        jobs = dict.fromkeys((name.removesuffix('.timer')+'.service' for name in names), 'inactive')
        mutations = []
        def command(*args):
            if args[:2] == ('systemctl', 'list-unit-files'):
                return ('other-ppi-watch.service enabled\n', 0)
            unit = args[2]
            if args[1] == 'is-enabled': return ('enabled', 0)
            if args[1] == 'is-active':
                if unit == 'other-ppi-watch.service': return ('active', 0)
                return (states.get(unit, jobs.get(unit, 'inactive')), 0)
            if args[1] == 'show':
                return (unit.removesuffix('.timer')+'.service', 0)
            if args[1] == 'stop':
                mutations.append(unit)
                states[unit] = 'inactive'
                return ('', 0)
            raise AssertionError(args)
        with TemporaryDirectory() as directory:
            with patch.object(suspend, 'ROOT', Path(directory)), patch.object(suspend, 'command', command):
                suspend.run('a'*40, 'suspend', self.policy())
                adopted = suspend.run('b'*40, 'suspend', self.policy())
                self.assertEqual(adopted['paused_from'], 'a'*40)
                self.assertEqual(len(mutations), 2)
                jobs['porota-preopen-rc6.service'] = 'active'
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_PREPAUSE_JOB_ACTIVE'):
                    suspend.run('c'*40, 'suspend', self.policy())
                self.assertFalse((Path(directory)/('scheduler-suspend-'+'c'*40+'.json')).exists())

    def test_a141_adopts_only_original_a138_operator_paused_full_timer_set(self):
        names = suspend.timer_policy(base64.b64decode(self.policy()))
        prior_sha = suspend.FAILED_A138_SUSPENSION_CANDIDATE
        candidate = 'c'*40
        original_pause_sha = 'e'*64
        raw = base64.b64decode(self.policy())
        original = dict(candidate_sha=prior_sha,policy_sha256=suspend.hashlib.sha256(raw).hexdigest(),
                        timers=names,ppi_before=[],attempted=names,status='SUSPENDED',
                        operator_pause_timer=suspend.OPERATOR_PREOPEN_TIMER,
                        operator_pause_receipt_sha256=original_pause_sha,
                        recorded_at='2026-10-27T22:13:25+00:00')
        states = {n:('inactive','disabled') if n == suspend.OPERATOR_PREOPEN_TIMER
                  else ('inactive','enabled') for n in names}
        services = {n.removesuffix('.timer')+'.service':'inactive' for n in names}
        calls = []
        def command(*args):
            calls.append(args)
            if args[:2] == ('systemctl','list-unit-files'): return '',0
            if args[1] == 'is-active':
                return (states[args[2]][0] if args[2] in states
                        else services.get(args[2],'inactive')),0
            if args[1] == 'is-enabled':return states[args[2]][1],0
            if args[1] == 'show': return args[2].removesuffix('.timer')+'.service',0
            raise AssertionError('no systemd mutation allowed: '+str(args))
        with TemporaryDirectory() as directory:
            root=Path(directory)
            prior_path=root/('scheduler-suspend-'+prior_sha+'.json')
            prior_path.write_text(json.dumps(original))
            with patch.object(suspend,'ROOT',root),patch.object(suspend,'command',command),\
                 patch.object(suspend,'exact_operator_preopen_pause',return_value=original_pause_sha) as a125,\
                 patch.object(suspend,'operator_preopen_timer_disabled',return_value=True),\
                 patch.object(suspend,'require_rescue_degraded_hold',
                              return_value='RESCUE_A92_STOPPED_A138_UNVALIDATED_HELD') as proof:
                row=suspend.run(candidate,'suspend',self.policy())
                self.assertEqual(row['status'],'SUSPENDED')
                self.assertTrue(row['adopted_a138_operator_hold'])
                self.assertEqual(row['paused_from'],prior_sha)
                self.assertEqual(row['operator_pause_receipt_sha256'],original_pause_sha)
                self.assertEqual(row['attempted'],names)
                self.assertEqual(row['prior_receipt_sha256'],
                                 suspend.hashlib.sha256(prior_path.read_bytes()).hexdigest())
                self.assertEqual(json.loads((root/('scheduler-suspend-'+candidate+'.json')).read_text()),row)
                self.assertEqual(suspend.run(candidate,'suspend',self.policy()),row)
                a125.assert_any_call(require_original_marker=True)
                proof.assert_called_once_with(candidate)
                self.assertFalse(any(x[1] in {'stop','start'} for x in calls))

    def test_a141_stale_receipt_running_job_and_changed_a125_fail_before_mutation(self):
        names=suspend.timer_policy(base64.b64decode(self.policy()))
        prior_sha=suspend.FAILED_A138_SUSPENSION_CANDIDATE
        digest=suspend.hashlib.sha256(base64.b64decode(self.policy())).hexdigest()
        original=dict(candidate_sha=prior_sha,policy_sha256=digest,timers=names,
                      ppi_before=[],attempted=names,status='SUSPENDED',
                      operator_pause_timer=suspend.OPERATOR_PREOPEN_TIMER,
                      operator_pause_receipt_sha256='e'*64)
        jobs={n.removesuffix('.timer')+'.service':'inactive' for n in names}
        states={n:('inactive','disabled') if n==suspend.OPERATOR_PREOPEN_TIMER
                else ('inactive','enabled') for n in names}
        def command(*args):
            if args[:2]==('systemctl','list-unit-files'):return '',0
            if args[1]=='is-active':return (states[args[2]][0] if args[2] in states
                                              else jobs.get(args[2],'inactive')),0
            if args[1]=='is-enabled':return states[args[2]][1],0
            if args[1]=='show':return args[2].removesuffix('.timer')+'.service',0
            raise AssertionError('no systemd mutation allowed')
        with TemporaryDirectory() as directory:
            root=Path(directory)
            prior_path=root/('scheduler-suspend-'+prior_sha+'.json')
            target=root/('scheduler-suspend-'+'c'*40+'.json')
            with patch.object(suspend,'ROOT',root),patch.object(suspend,'command',command),\
                 patch.object(suspend,'exact_operator_preopen_pause',return_value='e'*64) as a125,\
                 patch.object(suspend,'operator_preopen_timer_disabled',return_value=True),\
                 patch.object(suspend,'require_rescue_degraded_hold',
                              return_value='RESCUE_A92_STOPPED_A138_UNVALIDATED_HELD') as proof:
                for changed in (dict(original,status='RESTORED'),
                                dict(original,candidate_sha='f'*40),
                                dict(original,policy_sha256='0'*64),
                                dict(original,operator_pause_receipt_sha256='0'*64),
                                dict(original,attempted=[])):
                    prior_path.write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError,'SCHEDULER_A141_A138_RECEIPT_CHANGED'):
                        suspend.run('c'*40,'suspend',self.policy())
                    self.assertFalse(target.exists())
                prior_path.write_text(json.dumps(original))
                jobs[names[0].removesuffix('.timer')+'.service']='active'
                with self.assertRaisesRegex(ValueError,'SCHEDULER_A141_TIMER_OR_JOB_CHANGED'):
                    suspend.run('c'*40,'suspend',self.policy())
                self.assertFalse(target.exists())
                jobs[names[0].removesuffix('.timer')+'.service']='inactive'
                a125.side_effect=ValueError('SCHEDULER_OPERATOR_PAUSE_DAY_EXPIRED')
                with self.assertRaisesRegex(ValueError,'SCHEDULER_OPERATOR_PAUSE_DAY_EXPIRED'):
                    suspend.run('c'*40,'suspend',self.policy())
                self.assertFalse(target.exists())
                a125.side_effect=None
                proof.return_value='RESCUE_NO_DRAIN_A92_UNVALIDATED_HELD'
                with self.assertRaisesRegex(ValueError,'SCHEDULER_A141_A92_STOP_PROOF_RED'):
                    suspend.run('c'*40,'suspend',self.policy())
                self.assertFalse(target.exists())

    def test_failed_pretransfer_open_keeps_exact_paused_timers_without_rearming(self):
        names = {'porota-runtime-evidence-rc6.timer', 'porota-preopen-rc6.timer'}
        states = dict.fromkeys(names, 'active')
        mutations = []
        def command(*args):
            if args[:2] == ('systemctl', 'list-unit-files'):
                return ('other-ppi-watch.service enabled\n', 0)
            unit = args[2]
            if args[1] == 'is-enabled': return ('enabled', 0)
            if args[1] == 'is-active':
                return ('active', 0) if unit == 'other-ppi-watch.service' else (states.get(unit, 'inactive'), 0)
            if args[1] == 'show': return (unit.removesuffix('.timer')+'.service', 0)
            if args[1] == 'stop':
                mutations.append(('stop',unit)); states[unit]='inactive'; return ('',0)
            if args[1] == 'start': raise AssertionError('stopped runtime must not rearm')
            raise AssertionError(args)
        with TemporaryDirectory() as directory:
            with patch.object(suspend, 'ROOT', Path(directory)), \
                 patch.object(suspend, 'command', command), \
                 patch.object(suspend, 'require_exact_stopped_open_flat') as proof:
                suspend.run('a'*40, 'suspend', self.policy())
                result = suspend.run('a'*40, 'hold-open-flat', self.policy())
                self.assertEqual(result['status'], 'SUSPENDED')
                proof.assert_called_once_with('a'*40)
                self.assertEqual(len(mutations), 2)
                self.assertEqual(json.loads((Path(directory)/('scheduler-suspend-'+'a'*40+'.json')).read_text())['status'], 'SUSPENDED')
                proof.side_effect = ValueError('SCHEDULER_STOPPED_OPEN_FLAT_RED')
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_STOPPED_OPEN_FLAT_RED'):
                    suspend.run('a'*40, 'hold-open-flat', self.policy())
                proof.side_effect = None
                states['porota-preopen-rc6.timer'] = 'active'
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_OPEN_HOLD_TIMER_CHANGED'):
                    suspend.run('a'*40, 'hold-open-flat', self.policy())

    def test_open_recovery_selects_current_docker_pair_before_timer_mutation(self):
        names = {'porota-runtime-evidence-rc6.timer', 'porota-preopen-rc6.timer'}
        states = dict.fromkeys(names, 'active')
        docker = {'porota_production_observer':'exited',
                  'porota_production_dashboard':'exited'}
        mutations = []
        def command(*args):
            if args[0] == 'docker':
                return docker[args[-1]], 0
            if args[:2] == ('systemctl', 'list-unit-files'):
                return ('other-ppi-watch.service enabled\n', 0)
            unit = args[2]
            if args[1] == 'is-enabled': return ('enabled', 0)
            if args[1] == 'is-active':
                return ('active', 0) if unit == 'other-ppi-watch.service' else (states.get(unit,'inactive'), 0)
            if args[1] == 'show': return (unit.removesuffix('.timer')+'.service', 0)
            if args[1] in ('start','stop'):
                mutations.append((args[1],unit))
                states[unit] = 'active' if args[1] == 'start' else 'inactive'
                return ('',0)
            raise AssertionError(args)
        with TemporaryDirectory() as directory:
            with patch.object(suspend,'ROOT',Path(directory)), \
                 patch.object(suspend,'command',side_effect=command), \
                 patch.object(suspend,'require_exact_stopped_open_flat') as exact, \
                 patch.object(suspend,'require_owned_running') as running:
                suspend.run('a'*40,'suspend',self.policy())
                assert suspend.run('a'*40,'recover-open',self.policy())['status']=='SUSPENDED'
                exact.assert_called_once_with('a'*40)
                self.assertEqual(len(mutations),2)
                docker['porota_production_observer']='running'
                with self.assertRaisesRegex(ValueError,'SCHEDULER_OPEN_RECOVERY_MIXED_OR_UNKNOWN'):
                    suspend.run('a'*40,'recover-open',self.policy())
                self.assertEqual(len(mutations),2)
                docker['porota_production_dashboard']='running'
                assert suspend.run('a'*40,'recover-open',self.policy())['status']=='RESTORED'
                running.assert_called_once()
                self.assertEqual(len(mutations),4)

    def test_closed_recovery_holds_stopped_pair_and_rejects_mixed(self):
        names = {'porota-runtime-evidence-rc6.timer', 'porota-preopen-rc6.timer'}
        states = dict.fromkeys(names, 'active')
        docker = dict.fromkeys(('porota_production_observer',
                                'porota_production_dashboard'), 'exited')
        mutations = []
        def command(*args):
            if args[0] == 'docker': return docker[args[-1]], 0
            if args[:2] == ('systemctl', 'list-unit-files'):
                return ('other-ppi-watch.service enabled\n', 0)
            unit = args[2]
            if args[1] == 'is-enabled': return ('enabled', 0)
            if args[1] == 'is-active':
                return ('active', 0) if unit == 'other-ppi-watch.service' else (states.get(unit, 'inactive'), 0)
            if args[1] == 'show': return (unit.removesuffix('.timer')+'.service', 0)
            if args[1] in ('start', 'stop'):
                mutations.append((args[1], unit))
                states[unit] = 'active' if args[1] == 'start' else 'inactive'
                return ('', 0)
            raise AssertionError(args)
        with TemporaryDirectory() as directory:
            with patch.object(suspend, 'ROOT', Path(directory)), \
                 patch.object(suspend, 'command', side_effect=command), \
                 patch.object(suspend, 'require_exact_stopped_closed_flat') as exact, \
                 patch.object(suspend, 'require_owned_running') as running:
                suspend.run('a'*40, 'suspend', self.policy())
                assert suspend.run('a'*40, 'recover-closed', self.policy())['status'] == 'SUSPENDED'
                exact.assert_called_once_with('a'*40)
                self.assertEqual(len(mutations), 2)
                docker['porota_production_observer'] = 'running'
                with self.assertRaisesRegex(ValueError, 'SCHEDULER_CLOSED_RECOVERY_MIXED_OR_UNKNOWN'):
                    suspend.run('a'*40, 'recover-closed', self.policy())
                self.assertEqual(len(mutations), 2)
                docker['porota_production_dashboard'] = 'running'
                assert suspend.run('a'*40, 'recover-closed', self.policy())['status'] == 'RESTORED'
                running.assert_called_once()
                self.assertEqual(len(mutations), 4)

    def test_invalid_policy_never_mutates_systemd(self):
        bad = base64.b64encode(json.dumps({'units': {
            'systemd/porota-ppi-watch-rc6.timer': {'role': 'ACTIVE_TIMER', 'active': True},
        }}).encode()).decode()
        with patch.object(suspend, 'command', side_effect=AssertionError('systemctl called')):
            with self.assertRaisesRegex(ValueError, 'SCHEDULER_POLICY_UNSAFE'):
                suspend.run('b'*40, 'suspend', bad)


if __name__ == '__main__':
    unittest.main()
