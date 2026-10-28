"""Adversarial checks for the isolated, aggregate-only A154 host probe."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_a154_runtime_family_readonly as probe


class ProbeTests(unittest.TestCase):
    def test_a162_exact_invocation_decodes_preopen_red_checks_without_sensitive_fields(self):
        start = datetime(2026, 10, 27, 12, 9, 0, tzinfo=timezone.utc)
        payload = {'schema': 'POROTA_RC6_PREOPEN_V3', 'status': 'RED',
                   'red_checks': ['required_rc6_timers', 'observer_db'],
                   'checks': {'required_rc6_timers': {'state': 'RED', 'reason': 'PREOPEN_OPERATOR_PAUSE_DAY_EXPIRED',
                                                     'private_secret': 'DONT_PUBLISH'},
                              'observer_db': {'state': 'RED', 'detail': 'ACCOUNT_PRIVATE'}},
                   'secret': 'DONT_PUBLISH'}
        lines = json.dumps(payload, indent=2).splitlines()
        messages = [(start + timedelta(seconds=i / 100), line) for i, line in enumerate(lines)]
        output = probe._failure_reports(messages, unit='porota-preopen-rc6.service',
                                        started=start, ended=start + timedelta(seconds=30))
        self.assertEqual(output['red_checks'], ['observer_db', 'required_rc6_timers'])
        self.assertEqual(output['check_reasons']['required_rc6_timers'],
                         'PREOPEN_OPERATOR_PAUSE_DAY_EXPIRED')
        self.assertNotIn('ACCOUNT_PRIVATE', json.dumps(output))
        self.assertNotIn('DONT_PUBLISH', json.dumps(output))

    def test_a162_local_eod_reason_is_exact_and_stale_report_does_not_count(self):
        start = datetime(2026, 10, 27, 22, 10, 0, tzinfo=timezone.utc)
        payload = {'schema': 'rc6.local-eod.v1', 'state': 'BLOCKED',
                   'reason': 'RUNTIME_CLOSED_UNVERIFIED', 'account': 'DONT_PUBLISH'}
        # systemctl start/end are whole seconds, journald preserves fractions.
        messages = [(start + timedelta(seconds=10, milliseconds=400), json.dumps(payload))]
        output = probe._failure_reports(messages, unit='porota-local-eod-history-rc6.service',
                                        started=start, ended=start + timedelta(seconds=10))
        self.assertEqual(output['reason'], 'RUNTIME_CLOSED_UNVERIFIED')
        self.assertEqual(output['classification'], 'MATCHED')
        self.assertNotIn('DONT_PUBLISH', json.dumps(output))
        stale = probe._failure_reports(messages, unit='porota-local-eod-history-rc6.service',
                                       started=start + timedelta(hours=1),
                                       ended=start + timedelta(hours=1, seconds=10))
        self.assertEqual(stale['classification'], 'NO_VERIFICADO')
        payload['reason'] = 'ACCOUNT_PRIVATE'
        unknown = probe._failure_reports([(start, json.dumps(payload))],
                                         unit='porota-local-eod-history-rc6.service',
                                         started=start, ended=start + timedelta(seconds=1))
        self.assertEqual(unknown['reason'], 'EOD_FAILURE_UNCLASSIFIED')

    def test_a162_systemd_exit2_and_timestamp_must_match_report(self):
        started = 'Tue 2026-10-27 22:10:00 UTC'
        ended = 'Tue 2026-10-27 22:10:15 UTC'
        props = ('LoadState=loaded\nActiveState=failed\nResult=exit-code\nExecMainStatus=2\n'
                 f'ExecMainStartTimestamp={started}\nExecMainExitTimestamp={ended}\n')
        report = {'schema': 'rc6.local-eod.v1', 'state': 'BLOCKED', 'reason': 'OPEN_POSITIONS'}
        with patch.object(probe, 'command', return_value=props), \
             patch.object(probe, '_journal_messages', return_value=[
                 (datetime(2026, 10, 27, 22, 10, 5, tzinfo=timezone.utc), json.dumps(report))]):
            output = probe.failed_unit_reason('porota-local-eod-history-rc6.service')
        self.assertEqual(output['exit'], '2')
        self.assertEqual(output['reason'], 'OPEN_POSITIONS')
        with patch.object(probe, 'command', return_value=props.replace('ExecMainStatus=2', 'ExecMainStatus=0')), \
             patch.object(probe, '_journal_messages', side_effect=AssertionError('journal read on success')):
            output = probe.failed_unit_reason('porota-local-eod-history-rc6.service')
        self.assertEqual(output['reason'], 'EXIT2_INVOCATION_UNPROVEN')
        with self.assertRaisesRegex(ValueError, 'UNIT_NOT_ALLOWED'):
            probe.failed_unit_reason('ppi-watch.service')

    def test_a162_wrong_product_identity_stops_before_systemd(self):
        marker = {'candidate_sha': 'a' * 40, 'deploy_sha': probe.EXPECTED_A155_DEPLOY,
                  'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
                  'real_order_routes': 'NOT_CALLED'}
        with patch.object(probe, 'metadata', side_effect=[probe.EXPECTED_DROPLET_ID,
                                                         probe.EXPECTED_DROPLET_REGION]), \
             patch.object(probe, 'safe_json', return_value=marker), \
             patch.object(probe, 'failed_unit_reason', side_effect=AssertionError('unit read')):
            with self.assertRaisesRegex(ValueError, 'A155_PRODUCT_IDENTITY_CHANGED'):
                probe.unit_exit2_forensics()

    def test_shadow_envelope_digest_and_tamper_rejection(self):
        payload = {'generation_id': 'a' * 64, 'status': 'SHADOW_OBSERVING',
                   'real_orders_sent': 0, 'real_routes': 'NOT_CALLED'}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
                           separators=(',', ':')).encode('ascii')).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'CURRENT.json'
            path.write_text(json.dumps({'payload': payload, 'digest': digest}))
            self.assertEqual(probe.shadow_payload(path)['generation_id'], 'a' * 64)
            payload['status'] = 'TAMPERED'
            path.write_text(json.dumps({'payload': payload, 'digest': digest}))
            with self.assertRaisesRegex(ValueError, 'SHADOW_DIGEST_MISMATCH'):
                probe.shadow_payload(path)

    def test_iol_cache_projection_omits_records_and_signals_missing(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.json'
            now = datetime(2026, 10, 28, 14, tzinfo=timezone.utc)
            path.write_text(json.dumps({'cache_state': 'CACHE_STALE',
                'last_known_good_at': '2026-10-27T21:50:00Z',
                'continuation_state': 'CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL',
                'section_states': {'fci': 'CACHE_STALE'},
                'records': [{'symbol': 'PRIVATE_SYMBOL', 'account': 'PRIVATE_ACCOUNT'}]}))
            output = probe.iol_cache_probe(path, now)
            self.assertEqual(output['cache_state'], 'CACHE_STALE')
            self.assertNotIn('PRIVATE_SYMBOL', json.dumps(output))
            path.unlink()
            missing = probe.iol_cache_probe(path, now)
            self.assertEqual(missing['cache_state'], 'NO_VERIFICADO')
            self.assertEqual(missing['read_state'], 'FILENOTFOUNDERROR')

    def test_failed_unit_logs_are_classified_without_exposing_lines(self):
        raw = 'Account=PRIVATE_ACCOUNT\nHOST_RESERVE_REQUIRED\n'
        with patch.object(probe, 'command', return_value=raw):
            result = probe.journal_classes('porota-local-eod-history-rc6.service')
        self.assertEqual(result['classes'], ['HOST_RESERVE_REQUIRED'])
        self.assertNotIn('PRIVATE_ACCOUNT', json.dumps(result))
        with self.assertRaisesRegex(ValueError, 'JOURNAL_UNIT_NOT_ALLOWED'):
            probe.journal_classes('ppi-watch.service')

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
