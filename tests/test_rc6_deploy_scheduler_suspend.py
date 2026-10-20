import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import rc6_deploy_scheduler_suspend as suspend


class SuspendTests(unittest.TestCase):
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
            with patch.object(suspend, 'ROOT', Path(directory)), patch.object(suspend, 'command', command):
                result = suspend.run('a'*40, 'suspend', self.policy())
                self.assertEqual(result['status'], 'SUSPENDED')
                self.assertEqual({action for action, _ in mutations}, {'stop'})
                result = suspend.run('a'*40, 'restore', self.policy())
                self.assertEqual(result['status'], 'RESTORED')
                self.assertEqual({action for action, _ in mutations}, {'stop', 'start'})
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
