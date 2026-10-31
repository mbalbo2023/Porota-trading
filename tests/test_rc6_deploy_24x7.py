"""A229: real offline technical-admission paths, no host or paper trading.

Uses unittest so the authoritative weekend/holiday and forged-proof checks
remain runnable even on an isolated runner without third-party pytest.
"""
import contextlib
from datetime import datetime, timedelta, timezone, date
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts import rc6_deploy_quiescence as q


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


class TestWorkflowA92RealClassifierOffline(unittest.TestCase):
    """Execute the production inline A92 classifier with mocked host I/O."""

    @staticmethod
    def a92_code():
        source=(REPO/'.github/workflows/porota-deploy-v2-promote.yml').read_text()
        start="elif statuses=={'running'} and state in (\n                  ('DEGRADED','BOOT_CALENDAR'), ('STOPPED','CLOSED')):"
        block=source.split(start,1)[1].split(
            "\n          elif (statuses=={'exited'}",1)[0]
        return 'if True:\n'+block

    def run_classifier(self, when, *, paused=True, source_sha=None, flat=True):
        with tempfile.TemporaryDirectory(prefix='rc6-a229-classifier-') as tmp:
            root=Path(tmp)
            shutil.copyfile(REPO/'ak_byma_calendar.py',root/'ak_byma_calendar.py')
            db=root/'data/paper_v17/observer_v17.db'
            db.parent.mkdir(parents=True)
            marker=root/'data/deploy/CURRENT_STATE_V2.json'
            marker.parent.mkdir(parents=True)
            health=root/'data/paper_v17/artifacts/observer_v17.db/runtime-health.json'
            health.parent.mkdir(parents=True)
            IncidentClock.current=when
            marker.write_text(json.dumps({
                'recorded_at':(when-timedelta(seconds=5)).isoformat(),
                'schema_version':2,'candidate_sha':source_sha or q.RESCUE_SOURCE_SHA,
                'image_id':q.RESCUE_IMAGE_ID,'validation_status':'VALIDATED_RUNTIME',
                'mode':'PRODUCTION_PAPER','real_orders_sent':0,
                'real_order_routes':'NOT_CALLED'}))
            health.write_text(json.dumps({
                'recorded_at':(when-timedelta(seconds=5)).isoformat(),
                'schema':'rc6.runtime-child-health.v1',
                'source_sha':q.RESCUE_SOURCE_SHA,'mode':'PRODUCTION_PAPER',
                'real_orders_sent':0,'real_routes':'NOT_CALLED'}))
            with sqlite3.connect(db) as conn:
                conn.execute('CREATE TABLE observer_state '
                             '(id INTEGER,mode TEXT,real_orders_sent INTEGER,'
                             'heartbeat_at TEXT,process_state TEXT,session_state TEXT)')
                conn.execute('INSERT INTO observer_state VALUES (1,?,?,?,?,?)',
                             ('PRODUCTION_PAPER',0,
                              (when-timedelta(seconds=5)).isoformat(),
                              'DEGRADED','BOOT_CALENDAR'))
                for table in ('paper_positions','paper_cauciones','paper_future_positions'):
                    conn.execute(f'CREATE TABLE {table} (status TEXT)')
                if not flat:
                    conn.execute("INSERT INTO paper_positions VALUES ('OPEN')")
            originals={name:{'Id':'a'*64,'Image':q.RESCUE_IMAGE_ID,
                             'State':{'Status':'running'}} for name in q.OWNED}
            # Staged operator code stands in for exact SHA-checked candidate
            # bytes. Its denial and disabled-timer modes are exercised below.
            staged_operator=(
                'def exact_operator_preopen_pause(*,historical_hold_only=False, '
                'require_original_marker=False):\n'
                '    if not historical_hold_only: raise ValueError("HISTORICAL_REQUIRED")\n'
                '    return "e"*64\n'
                f'def operator_preopen_timer_disabled(): return {paused!r}\n'
            ).encode()
            env={'ROOT':root,'OWNED':q.OWNED,'state':('DEGRADED','BOOT_CALENDAR'),
                 'statuses':{'running'},'originals':originals,'path':db,
                 'inspect':lambda name:originals[name],
                 'staged_operator_source':lambda:staged_operator,
                 'types':types,'sys':sys,'sqlite3':sqlite3}
            fake_date=types.ModuleType('datetime')
            fake_date.datetime=IncidentClock
            fake_date.date=date
            fake_date.timezone=timezone
            with patch.dict(sys.modules,{'datetime':fake_date}),patch.object(
                    q,'critical_admission',return_value={'capability':'issues_only'}) as critical:
                out=io.StringIO()
                with contextlib.redirect_stdout(out):
                    try:
                        exec(compile(self.a92_code(),'deploy-inline-a92','exec'),env)
                    except SystemExit as exc:
                        return str(exc),critical.call_count
                return out.getvalue().strip(),critical.call_count

    def test_sunday_and_byma_holiday_execute_inline_classifier(self):
        for when in (SUNDAY,HOLIDAY):
            with self.subTest(when=when.isoformat()):
                label,critical=self.run_classifier(when)
                self.assertEqual(label,q.RESCUE_MODE)
                self.assertEqual(critical,1)

    def test_inline_classifier_denies_rearm_forged_marker_and_open_book(self):
        for kwargs,error in (({'paused':False},'RC6_RESCUE_24X7_NO_GO_RED'),
                             ({'source_sha':'f'*40},'RC6_RESCUE_CURRENT_SOURCE_RED'),
                             ({'flat':False},'RC6_RESCUE_BOOK_NOT_FLAT')):
            with self.subTest(error=error):
                label,critical=self.run_classifier(SUNDAY,**kwargs)
                self.assertEqual(label,error)
                self.assertEqual(critical,0)


if __name__=='__main__':
    unittest.main()
