"""An IDLE attempt barrier is retired only against an unchanged PAPER host."""
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_pre_suspend_fingerprint as proof


class Response:
    status = 200

    def __init__(self, data):
        self.data = BytesIO(data)

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, length): return self.data.read(length)


class Opener:
    def open(self, url, timeout):
        assert timeout == 3
        return Response(b'594077619\n' if url.endswith('/id') else b'nyc1\n')


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rc6-pre-suspend-')
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'paper.db'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE observer_state (id INTEGER,mode TEXT,real_orders_sent INTEGER)')
            db.execute("INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',0)")
            for table in ('paper_positions','paper_cauciones','paper_future_positions'):
                db.execute('CREATE TABLE ' + table + ' (status TEXT)')
        p = patch.object(proof, 'DB', self.db)
        p.start(); self.addCleanup(p.stop)
        self.policy = json.dumps({'units': {
            f'systemd/porota-fixture-{n:02d}.timer':
                {'role': 'ACTIVE_TIMER', 'active': True} for n in range(16)
        }}).encode()
        self.held = {f'porota-fixture-{n:02d}.timer': 'inactive'
                     for n in range(16)}
        self.images = {f'{n:064x}': f'sha256:{n:064x}' for n in (1,2,3)}
        self.calls = []

    def command(self, *args, allowed=(0,)):
        self.calls.append(args)
        if args[:3] == ('docker', 'ps', '-aq'):
            return '\n'.join(self.images)
        if args[:3] == ('docker', 'inspect', '--format'):
            cid = args[-1]
            n = list(self.images).index(cid)
            name = sorted(proof.OWNED)[n]
            return '|'.join(json.dumps(value) for value in (
                cid, '/' + name, self.images[cid], 'running', 0,
                '2026-10-28T12:00:00Z'))
        if args[:2] == ('systemctl', 'list-unit-files'):
            return 'ppi-watch.service enabled'
        if args[:2] == ('systemctl', 'show'):
            return args[2].replace('.timer', '.service')
        if args[:2] == ('systemctl', 'is-active'):
            return self.held[args[2]] if args[2] in self.held else (
                'active' if args[2] == 'ppi-watch.service' else 'inactive')
        if args[:2] == ('systemctl', 'is-enabled'):
            return 'enabled'
        raise AssertionError(f'No mutating or unowned command allowed: {args}')

    def test_sixteen_held_timers_and_all_containers_exact(self):
        with patch.object(proof, 'command', side_effect=self.command):
            baseline = proof.fingerprint(self.policy, opener=Opener())
            self.assertEqual(proof.fingerprint(self.policy, opener=Opener()), baseline)
            self.held['porota-fixture-05.timer'] = 'active'
            self.assertNotEqual(proof.fingerprint(self.policy, opener=Opener()), baseline)
            self.held['porota-fixture-05.timer'] = 'inactive'
            self.held['porota-fixture-05.service'] = 'active'
            with self.assertRaisesRegex(proof.FingerprintRejected, 'TIMER_SERVICE_WRITER_ACTIVE'):
                proof.fingerprint(self.policy, opener=Opener())
            self.held.pop('porota-fixture-05.service')
            self.images['2'.zfill(64)] = 'sha256:' + 'f' * 64
            self.assertNotEqual(proof.fingerprint(self.policy, opener=Opener()), baseline)
        self.assertFalse(any(args[:2] in {('systemctl','start'),('systemctl','stop'),
                                          ('docker','rm')} for args in self.calls))

    def test_paper_order_or_position_change_rejects_even_same_containers(self):
        with patch.object(proof, 'command', side_effect=self.command):
            baseline = proof.fingerprint(self.policy, opener=Opener())
            with sqlite3.connect(self.db) as db:
                db.execute('UPDATE observer_state SET real_orders_sent=1 WHERE id=1')
            with self.assertRaisesRegex(proof.FingerprintRejected, 'PAPER_SAFETY_RED'):
                proof.fingerprint(self.policy, opener=Opener())
            with sqlite3.connect(self.db) as db:
                db.execute('UPDATE observer_state SET real_orders_sent=0 WHERE id=1')
                db.execute("INSERT INTO paper_cauciones VALUES ('OPEN')")
            with self.assertRaisesRegex(proof.FingerprintRejected, 'PAPER_POSITION_NOT_FLAT'):
                proof.fingerprint(self.policy, opener=Opener())
        self.assertEqual(len(baseline), 64)


if __name__ == '__main__':
    unittest.main()
