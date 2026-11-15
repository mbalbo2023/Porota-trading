"""A229: real offline technical-admission paths, no host or paper trading.

Uses unittest so the authoritative weekend/holiday and forged-proof checks
remain runnable even on an isolated runner without third-party pytest.
"""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import shutil
import hashlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_deploy_quiescence as q
from scripts import porota_rootless_host as host
from scripts import porota_operator_intent as intent


REPO = Path(__file__).resolve().parents[1]
SUNDAY = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)
HOLIDAY = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)
ANCHOR = 'e' * 64


class IncidentClock(datetime):
    current = SUNDAY

    @classmethod
    def now(cls, tz=None):
        return (cls.current.astimezone(tz) if tz else
                cls.current.replace(tzinfo=None))


class TestTechnicalRescueProof(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rc6-a229-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copyfile(REPO/'ak_byma_calendar.py', self.root/'ak_byma_calendar.py')
        IncidentClock.current = SUNDAY
        self.timer_disabled = True
        for name, value in (('ROOT',self.root),('datetime',IncidentClock)):
            mocked = patch.object(q,name,value)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(q,'operator_paper_no_go_proof',
                              side_effect=self.no_go)
        mocked.start()
        self.addCleanup(mocked.stop)

    def no_go(self, *, historical_hold_only=False, **_):
        if not historical_hold_only or not self.timer_disabled:
            raise ValueError('PINNED_NO_GO_OR_LIVE_TIMER_REQUIRED')
        return ANCHOR

    def rescue_receipt(self, proof):
        return {'degraded_closed_rescue':True,
                'rescue_pre_state':['DEGRADED','BOOT_CALENDAR'],
                'rescue_source_sha':q.RESCUE_SOURCE_SHA,
                'rescue_image_id':q.RESCUE_IMAGE_ID,
                'rescue_calendar_and_hours':proof,
                'containers':{name:{'image':q.RESCUE_IMAGE_ID}
                              for name in q.OWNED},
                'recorded_at':IncidentClock.current.isoformat()}

    def test_sunday_and_byma_holiday_positive_via_real_receipt_verifier(self):
        for when in (SUNDAY,HOLIDAY):
            with self.subTest(when=when.isoformat()):
                IncidentClock.current = when
                proof=q.degraded_closed_rescue_proof()
                self.assertTrue(proof['technical_24x7'])
                self.assertEqual(proof['technical_hold'],q.TECHNICAL_24X7_HOLD)
                self.assertFalse(proof['paper_execution_authorized'])
                self.assertFalse(proof['operator_paper_early'])
                self.assertEqual(proof['historical_no_go_sha256'],ANCHOR)
                self.assertIs(q.exact_rescue_receipt(self.rescue_receipt(proof)),proof)
                adopted=q.adopted_rescue_closed_proof()
                self.assertTrue(adopted['technical_24x7'])
                self.assertEqual(adopted['historical_no_go_sha256'],ANCHOR)

    def test_off_hours_never_bypass_live_timer_or_pinned_calendar(self):
        self.timer_disabled=False
        with self.assertRaisesRegex(ValueError,'RESCUE_24X7_NO_GO_RED'):
            q.degraded_closed_rescue_proof()
        with self.assertRaisesRegex(ValueError,'ADOPTED_24X7_NO_GO_RED'):
            q.adopted_rescue_closed_proof()
        self.timer_disabled=True
        (self.root/'ak_byma_calendar.py').write_text('forged calendar')
        with self.assertRaisesRegex(ValueError,'RESCUE_CALENDAR_SOURCE_RED'):
            q.degraded_closed_rescue_proof()

    def test_receipt_rechecks_pinned_calendar_after_initial_drain(self):
        receipt=self.rescue_receipt(q.degraded_closed_rescue_proof())
        (self.root/'ak_byma_calendar.py').write_text('forged after drain')
        with self.assertRaisesRegex(ValueError,'RESCUE_24X7_CALENDAR_CHANGED'):
            q.exact_rescue_receipt(receipt)

    def test_forged_mode_anchor_clock_and_paper_go_fail_closed(self):
        proof=q.degraded_closed_rescue_proof()
        for field, forged in (
                ('technical_24x7',False),
                ('technical_hold','TRUST_ME'),
                ('historical_no_go_sha256','f'*64),
                ('preopen_timer_disabled',False),
                ('paper_execution_authorized',True),
                ('business_day','2026-10-28'),
                ('operator_paper_early',True)):
            with self.subTest(field=field):
                altered=dict(proof,**{field:forged})
                with self.assertRaises(ValueError):
                    q.exact_rescue_receipt(self.rescue_receipt(altered))
        self.timer_disabled=False
        with self.assertRaisesRegex(ValueError,'RESCUE_24X7_HOLD_LOST'):
            q.exact_rescue_receipt(self.rescue_receipt(proof))


class TestRootlessTechnicalAdmissionOffline(unittest.TestCase):
    """Exercise the current probe/postflight, not the retired YAML A92 rescue.

    A92 required an already-paused timer and a flat book for that one rescue.
    Rootless quiesces its owned writers itself and preserves the existing DB;
    an OPEN PAPER position is not a generic technical-deployment veto. Operator
    admission remains separate and never follows from a healthy postflight.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rc6-24x7-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root/'home'
        self.home.mkdir(mode=0o700)
        self.identity = host.Identity(os.getuid(), os.getgid(), 'fixture', self.home)
        self.db = self.root/host.DB_RELATIVE
        self.db.parent.mkdir(parents=True)
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE observer_state '
                         '(id INTEGER,mode TEXT,real_orders_sent INTEGER,ppi_auth TEXT,'
                         'process_state TEXT,session_state TEXT,last_market_data_at TEXT,'
                         'heartbeat_at TEXT,detail TEXT)')
            conn.execute('INSERT INTO observer_state VALUES (1,?,?,?,?,?,?,?,?)',
                         ('PRODUCTION_PAPER',0,'OK','WAITING_MARKET','MARKET_CLOSED',
                          None,SUNDAY.isoformat(),''))
            conn.execute('CREATE TABLE paper_workspace (id INTEGER,dataset_id TEXT)')
            conn.execute("INSERT INTO paper_workspace VALUES (1,'preserved-paper-data')")
            for table in ('paper_positions','paper_cauciones','paper_future_positions'):
                conn.execute(f'CREATE TABLE {table} (status TEXT)')
                conn.execute(f"INSERT INTO {table} VALUES ('OPEN')")
        self.paused = {'schema':'RC6_OPERATOR_PAPER_INTENT_V2',
                       'status':'PAPER_PAUSED_BY_OPERATOR','candidate_sha':'a'*40,
                       'candidate_tree_sha':'b'*40,'operator_comment_id':123,
                       'operator_github_login':'mbalbo2023','rearm_run_id':456,
                       'issued_at_ar':'2026-10-30T10:00:00-03:00'}
        self.operator_path = intent.private_directory(intent.directory(self.home)) / 'paper-rearm-v1.json'
        intent.atomic_write(self.operator_path, json.dumps(self.paused).encode())

    def postflight(self, when):
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE observer_state SET heartbeat_at=?', (when.isoformat(),))
        connect = sqlite3.connect
        def runtime_connect(database, **kwargs):
            self.assertEqual(database, 'file:/app/data/paper_v17/observer_v17.db?mode=ro')
            self.assertIs(kwargs.get('uri'), True)
            return connect(self.db.as_uri()+'?mode=ro', **kwargs)
        def docker_json(*args):
            self.assertEqual(args, ('container','exec','1'*64,'python','-c',host.RUNTIME_PROBE))
            out = io.StringIO()
            with patch.object(sqlite3,'connect',side_effect=runtime_connect), contextlib.redirect_stdout(out):
                # Execute the same read-only SQL and real-order route guard used
                # inside the deployed image; only Docker/filesystem I/O is local.
                exec(compile(host.RUNTIME_PROBE,'rootless-runtime-probe','exec'), {})
            return json.loads(out.getvalue())
        manager = host.Host(self.root, self.identity)
        IncidentClock.current = when
        with patch.object(manager.docker,'json',side_effect=docker_json), \
             patch.object(manager,'check_role',return_value={
                 'State':{'Running':True,'Health':{'Status':'healthy'}}}), \
             patch.object(host,'datetime',IncidentClock):
            return manager.postflight({'roles':{'observer':'1'*64}},
                when-timedelta(seconds=1),budget=10,
                sleep=lambda seconds:self.fail('A fresh closed-session runtime must be ready'))

    def test_sunday_and_byma_holiday_verify_runtime_without_rearming_or_resetting_book(self):
        for when in (SUNDAY,HOLIDAY):
            with self.subTest(when=when.isoformat()):
                before = host.database_snapshot(self.db)
                operator_bytes = self.operator_path.read_bytes()
                result = self.postflight(when)
                self.assertEqual(result['observer'],'running')
                self.assertEqual(result['orders_guard'],'BLOCKED')
                self.assertEqual(result['paper_readiness'],'FAIL_CLOSED_PER_INSTRUMENT')
                self.assertEqual(host.database_snapshot(self.db),before)
                self.assertEqual(self.operator_path.read_bytes(),operator_bytes)
                self.assertEqual(intent.read_intent(self.home,now=when),self.paused)
                with sqlite3.connect(self.db) as conn:
                    for table in ('paper_positions','paper_cauciones','paper_future_positions'):
                        self.assertEqual(conn.execute(f'SELECT status FROM {table}').fetchall(),[('OPEN',)])

    def test_off_hours_do_not_bypass_private_provenance_or_real_order_safety(self):
        release = {'schema':'porota.release.v1','source_sha':'a'*40,'source_tree':'b'*40,
                   'image_id':'sha256:'+'c'*64,'operating_mode':'PRODUCTION_PAPER',
                   'execution_mode':'SIMULATION','real_order_capability':'BLOCKED',
                   'real_orders_sent_required':0}
        host.private_dir(self.identity.state)
        host.private_dir(self.identity.state/'releases')
        directory = host.private_dir(self.identity.state/'releases'/release['source_sha'])
        raw = json.dumps(release).encode()
        host.atomic_write(directory/'release.json',raw)
        current = {**release,'schema':'porota.rootless.deploy.v1','state':'VALIDADO_RUNTIME',
                   'real_orders_sent':0,'release_manifest_sha256':hashlib.sha256(raw).hexdigest()}
        host.write_json(self.identity.state/'current.json',current)
        self.assertEqual(host.read_release_state(self.identity)[1],release)
        for when in (SUNDAY,HOLIDAY):
            with self.subTest(when=when.isoformat()):
                host.write_json(self.identity.state/'current.json',dict(current,source_tree='f'*40))
                with self.assertRaisesRegex(host.Hold,'PRIVATE_RELEASE_IDENTITY_MISMATCH'):
                    host.read_release_state(self.identity)
                intent.atomic_write(self.operator_path,json.dumps(dict(
                    self.paused,operator_github_login='untrusted')).encode())
                with self.assertRaisesRegex(ValueError,'OPERATOR_INTENT_PROVENANCE_INVALID'):
                    intent.read_intent(self.home,now=when)
                with sqlite3.connect(self.db) as conn:
                    conn.execute('UPDATE observer_state SET real_orders_sent=1')
                with self.assertRaisesRegex(host.Hold,'PAPER_DATABASE_SAFETY'):
                    host.database_snapshot(self.db)
                with self.assertRaisesRegex(host.Hold,'PAPER_RUNTIME_SAFETY'):
                    self.postflight(when)


if __name__=='__main__':
    unittest.main()
