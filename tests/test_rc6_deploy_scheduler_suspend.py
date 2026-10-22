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

    def test_invalid_policy_never_mutates_systemd(self):
        bad = base64.b64encode(json.dumps({'units': {
            'systemd/porota-ppi-watch-rc6.timer': {'role': 'ACTIVE_TIMER', 'active': True},
        }}).encode()).decode()
        with patch.object(suspend, 'command', side_effect=AssertionError('systemctl called')):
            with self.assertRaisesRegex(ValueError, 'SCHEDULER_POLICY_UNSAFE'):
                suspend.run('b'*40, 'suspend', bad)


if __name__ == '__main__':
    unittest.main()
