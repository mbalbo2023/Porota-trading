"""An IDLE attempt barrier is retired only against an unchanged PAPER host."""
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import time
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
            db.execute('CREATE TABLE paper_family_lifecycle(family TEXT,state TEXT)')
        p = patch.object(proof, 'DB', self.db)
        p.start(); self.addCleanup(p.stop)
        self.names = [f'porota-fixture-{n:02d}.timer' for n in range(15)] + [
            'porota-iol-oauth-health-rc6.timer']
        self.policy = json.dumps({'units': {
            'systemd/' + name: {'role': 'ACTIVE_TIMER', 'active': True}
            for name in self.names
        }}).encode()
        self.held = {name: 'inactive' for name in self.names}
        self.images = {f'{n:064x}': f'sha256:{n:064x}' for n in (1,2,3)}
        self.calls = []

    def command(self, *args, allowed=(0,), timeout=5):
        self.assertLessEqual(timeout, 5)
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
            self.held['porota-iol-oauth-health-rc6.service'] = 'active'
            active_guardian = proof.fingerprint(self.policy, opener=Opener())
            self.assertNotEqual(active_guardian, baseline)
            self.assertEqual(proof.fingerprint(self.policy, opener=Opener()),
                             active_guardian)
            self.held.pop('porota-iol-oauth-health-rc6.service')
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

    def test_active_fci_subscription_is_never_flat_or_a207_eligible(self):
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO paper_family_lifecycle VALUES('FCI','NAV_APPLIED')")
        with patch.object(proof, 'command', side_effect=self.command):
            with self.assertRaisesRegex(proof.FingerprintRejected,
                                        'FCI_PAPER_NAV_LIABILITY_NOT_FLAT'):
                proof.fingerprint(self.policy, opener=Opener())
            with self.assertRaisesRegex(proof.FingerprintRejected,
                                        'FCI_PAPER_NAV_LIABILITY_NOT_FLAT'):
                proof.fingerprint(self.policy, opener=Opener(), a207_authorized_open=True)

    def test_a207_explicit_readonly_fingerprint_includes_bounded_support(self):
        with sqlite3.connect(self.db) as db:
            db.execute('ALTER TABLE paper_positions ADD COLUMN paper_id TEXT')
            db.execute("INSERT INTO paper_positions(status,paper_id) VALUES('OPEN','PAPER-A207')")
            for table in ('paper_fills','paper_spot_sales','paper_exit_intents',
                          'paper_sale_receivables','paper_admin_cancellations',
                          'paper_learning_samples','paper_events'):
                db.execute('CREATE TABLE ' + table + ' (paper_id TEXT, detail TEXT)')
            db.execute("INSERT INTO paper_events VALUES('PAPER-A207','OPEN')")
        with patch.object(proof, 'command', side_effect=self.command):
            with self.assertRaisesRegex(proof.FingerprintRejected,'PAPER_POSITION_NOT_FLAT'):
                proof.fingerprint(self.policy, opener=Opener())
            before = proof.fingerprint(self.policy, opener=Opener(),
                                       a207_authorized_open=True)
            assert len(before) == 64
            with sqlite3.connect(self.db) as db:
                db.execute("UPDATE paper_events SET detail='CHANGED' WHERE paper_id='PAPER-A207'")
            after = proof.fingerprint(self.policy, opener=Opener(),
                                      a207_authorized_open=True)
            self.assertNotEqual(before, after)
            with sqlite3.connect(self.db) as db:
                db.execute('UPDATE observer_state SET real_orders_sent=1')
            with self.assertRaisesRegex(proof.FingerprintRejected,'PAPER_SAFETY_RED'):
                proof.fingerprint(self.policy, opener=Opener(),
                                  a207_authorized_open=True)
        self.assertFalse(any(args[:2] in {('systemctl','start'),('systemctl','stop'),
                                          ('docker','rm')} for args in self.calls))

    def test_readonly_snapshot_accepts_active_iol_guardian_and_continues_gate(self):
        self.held['porota-iol-oauth-health-rc6.service'] = 'active'
        with patch.object(proof, 'command', side_effect=self.command), \
             patch.object(proof.urllib.request, 'build_opener', return_value=Opener()):
            result = proof.main(['--policy-b64', __import__('base64').b64encode(
                self.policy).decode()])
        self.assertEqual(result, 0)
        self.assertEqual(self.held['porota-iol-oauth-health-rc6.service'], 'active')
        self.assertFalse(any(args[:2] in {('systemctl','start'),('systemctl','stop'),
                                          ('docker','rm')} for args in self.calls))

    def test_sqlite_scan_has_progress_budget_even_if_no_open_row(self):
        with sqlite3.connect(self.db) as db:
            db.executemany("INSERT INTO paper_positions VALUES ('CLOSED')",
                           [()] * 20000)
        start = time.monotonic()
        with patch.object(proof, 'SQL_BUDGET_SECONDS', 0), \
             patch.object(proof, 'command', side_effect=self.command):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'interrupted'):
                proof.fingerprint(self.policy, opener=Opener())
        self.assertLess(time.monotonic() - start, 2)


if __name__ == '__main__':
    unittest.main()
