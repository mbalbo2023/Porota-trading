"""Adversarial checks for the isolated, aggregate-only A154 host probe."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_a154_runtime_family_readonly as probe


class ProbeTests(unittest.TestCase):
    def test_wrong_droplet_stops_before_runtime_read(self):
        with patch.object(probe, 'metadata', side_effect=['123456789', 'nyc1']), \
             patch.object(probe, 'safe_json', side_effect=AssertionError('runtime accessed')):
            with self.assertRaisesRegex(ValueError, 'DROPLET_IDENTITY_MISMATCH'):
                probe.main('e30=')

    def test_non_numeric_counter_never_escapes(self):
        self.assertIsNone(probe.count('PRIVATE_SYMBOL'))
        self.assertIsNone(probe.count(-1))
        self.assertEqual(probe.count(3), 3)

    def test_metadata_endpoint_allowlist(self):
        with self.assertRaisesRegex(ValueError, 'METADATA_PATH_NOT_ALLOWED'):
            probe.metadata('user-data')

    def test_sql_budget_interrupts_unbounded_read(self):
        with sqlite3.connect(':memory:') as db:
            with self.assertRaises(sqlite3.OperationalError):
                probe.bounded_query(db, '''WITH RECURSIVE n(x) AS
                    (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1000000)
                    SELECT SUM(x) FROM n''', seconds=-1)

    def test_rejects_symlink_even_when_json_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / 'real.json'
            real.write_text('{}')
            alias = Path(tmp) / 'alias.json'
            alias.symlink_to(real)
            with self.assertRaisesRegex(ValueError, 'UNSAFE_JSON_PATH'):
                probe.safe_json(alias)

    def test_paper_zero_fails_closed_for_marker_or_database(self):
        with sqlite3.connect(':memory:') as db:
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE observer_state(id INTEGER,mode TEXT,real_orders_sent INTEGER,'
                       'process_state TEXT,session_state TEXT,ppi_auth TEXT,heartbeat_at TEXT)')
            db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,'STOPPED',"
                       "'MARKET_CLOSED','OK','2026-10-28T12:00:00Z')")
            marker = {'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
                      'real_order_routes': 'NOT_CALLED'}
            self.assertEqual(probe.safety(db, marker)['real_orders_sent'], 0)
            for changed in ({**marker, 'real_orders_sent': 1},
                            {**marker, 'real_order_routes': 'CALLED'},
                            {**marker, 'mode': 'LIVE'}):
                with self.assertRaisesRegex(ValueError, 'PAPER_ZERO_FAIL_CLOSED'):
                    probe.safety(db, changed)
            db.execute('UPDATE observer_state SET real_orders_sent=1 WHERE id=1')
            with self.assertRaisesRegex(ValueError, 'PAPER_ZERO_FAIL_CLOSED'):
                probe.safety(db, marker)

    def test_five_field_identity_join_and_aggregate_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / 'paper.db'
            with sqlite3.connect(dbpath) as db:
                db.executescript('''
                  CREATE TABLE financial_instrument_catalog(
                    ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
                    settlement TEXT,status TEXT,capability TEXT,last_seen_at TEXT);
                  CREATE TABLE candidate_identity_v2(
                    ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
                    settlement TEXT,status TEXT,can_simulate INTEGER);
                  CREATE TABLE catalog_family_coverage(
                    instrument_type TEXT,declared INTEGER,observed_count INTEGER,
                    ready_paper_count INTEGER,discovery_status TEXT,checked_at TEXT);
                  INSERT INTO financial_instrument_catalog VALUES
                    ('SENSITIVE_TICKER','ACCIONES','BYMA','ARS','T1','AVAILABLE',
                     'READY_PAPER_SPOT','2026-10-28T13:00:00Z'),
                    ('SENSITIVE_TICKER','ACCIONES','BYMA','USD','T1','AVAILABLE',
                     'READY_PAPER_SPOT','2026-10-28T13:00:00Z');
                  INSERT INTO candidate_identity_v2 VALUES
                    ('SENSITIVE_TICKER','ACCIONES','BYMA','ARS','T1','AVAILABLE',1),
                    ('SENSITIVE_TICKER','ACCIONES','BYMA','USD','T1','BLOCKED',0);
                  INSERT INTO catalog_family_coverage VALUES
                    ('ACCIONES',1,2,2,'OBSERVED','2026-10-28T13:00:00Z');
                ''')
            with patch.object(probe, 'DB', dbpath):
                output = probe.family_counts()
            self.assertEqual(output['catalog'][0]['discovered'], 2)
            self.assertEqual(output['catalog'][0]['candidates'], 1)
            self.assertNotIn('SENSITIVE_TICKER', json.dumps(output))

    def test_timer_policy_never_queries_ppi_watch_or_starts_services(self):
        units = {'systemd/porota-test-%02d-rc6.timer' % n:
                 {'role': 'ACTIVE_TIMER', 'active': True} for n in range(16)}
        units['systemd/porota-ppi-watch-rc6.timer'] = {'role': 'ACTIVE_TIMER',
                                                        'active': True}
        with self.assertRaisesRegex(ValueError, 'TIMER_POLICY_UNEXPECTED'):
            probe.timer_states({'units': units})
        units.pop('systemd/porota-ppi-watch-rc6.timer')
        called = []

        def fake(args, timeout=1):
            called.append(args)
            if args[2].endswith('.timer'):
                return 'Unit=' + args[2].replace('.timer', '.service') + '\nActiveState=inactive\n'
            return 'ActiveState=inactive\nResult=success\nExecMainStatus=0\n'

        with patch.object(probe, 'command', side_effect=fake):
            output = probe.timer_states({'units': units})
        self.assertEqual(len(output), 16)
        self.assertTrue(all(argv[:2] == ['systemctl', 'show'] for argv in called))
        self.assertFalse(any('ppi-watch' in ' '.join(argv) for argv in called))


if __name__ == '__main__':
    unittest.main()
