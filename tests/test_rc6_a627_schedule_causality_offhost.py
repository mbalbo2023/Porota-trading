"""A627 adversarial OFFHOST tests: no GitHub/provider/host request made."""
from __future__ import annotations

import unittest
import json
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re

from scripts.rc6_a627_schedule_causality_offhost import (
    WORKFLOW_ID, WORKFLOW_PATH, classify_cadence, clock,
    expected_watch_probe, genuine_schedule_run, slots_for_day, main,
)


DAY = date(2026, 11, 4)
SLOTS = slots_for_day(DAY)
UTC = timezone.utc


def run(slot, *, lag=30, run_id=100, event='schedule', status='completed',
        conclusion='success', attempt=1, start_lag=None):
    created=slot+timedelta(seconds=lag)
    started=slot+timedelta(seconds=(lag+30 if start_lag is None else start_lag))
    return {'id':run_id,'event':event,'head_branch':'main','path':WORKFLOW_PATH,
            'workflow_id':WORKFLOW_ID,'run_attempt':attempt,'status':status,
            'conclusion':conclusion,'created_at':created.isoformat(),
            'run_started_at':started.isoformat()}


def jobs(outcome='success', step='success'):
    return [{'name':'watch','status':'completed','conclusion':outcome,
             'steps':[{'name':'Collect bounded read-only host truth',
                       'status':'completed' if step!='skipped' else 'completed',
                       'conclusion':step}]}]


def audit(runs, jobs_by_run=None, as_of=None, coverage=True):
    return classify_cadence(runs,jobs_by_run or {},day_art=DAY,
                            as_of=as_of or datetime(2026,11,4,21,40,tzinfo=UTC),
                            coverage_complete=coverage)


def slot_record(report, slot):
    return next(r for r in report['slots'] if r['slot_utc']==slot.isoformat())


class ScheduleCausalityTest(unittest.TestCase):
    def test_independent_manual_fallback_action_no_host_or_provider(self):
        yaml=(Path(__file__).parents[1]/'.github/workflows/'/
              'rc6-a627-schedule-causality-offhost.yml').read_text(encoding='utf-8')
        crons=re.findall(r"^\s+- cron: '([^']+)'",yaml,re.MULTILINE)
        self.assertEqual([
            '50 13 4 11 *','25 14 4 11 *','35 17 4 11 *',
            '35 19 4 11 *','15 20 4 11 *','35 21 4 11 *'],crons)
        self.assertIn('workflow_dispatch:',yaml)
        self.assertIn('actions: read',yaml)
        self.assertNotIn('actions: write',yaml)
        self.assertNotIn('DO_SSH_KEY',yaml)
        self.assertNotIn('ssh -',yaml)
        self.assertNotIn('trading.portfoliopersonal.com',yaml)
        self.assertNotIn('rc6-market-watch-readonly.yml\n  push:',yaml)

    def test_a617_one_day_host_ro_probe_gate_and_utc_art_slots(self):
        yaml=(Path(__file__).parents[1]/'.github/workflows/'/
              'rc6-a617-ppi-origin-selector-readonly.yml').read_text(encoding='utf-8')
        crons=re.findall(r"^\s+- cron: '([^']+)'",yaml,re.MULTILINE)
        self.assertEqual(['05 14 4 11 *','50 17 4 11 *',
                          '50 19 4 11 *'],crons)
        # Product EOD local archive wakes every 5m from 18:00; candidate
        # postclose snapshot also wakes 18:20. No A617 host cron that minute.
        self.assertNotIn('20 21 4 11 *',crons)
        # The A630 #908 host slots are 10:45/11:20/14:30/16:30 ART.
        a630={'45 13 4 11 *','20 14 4 11 *',
              '30 17 4 11 *','30 19 4 11 *'}
        self.assertFalse(a630.intersection(crons))
        def art_minute(expr):
            minute,hour,*_=expr.split()
            return ((int(hour)-3)%24)*60+int(minute)
        self.assertGreaterEqual(min(abs(art_minute(left)-art_minute(right))
            for left in crons for right in a630),15)
        self.assertIn('  host_provenance:\n',yaml)
        host_section=yaml.split('  host_provenance:\n',1)[1]
        self.assertIn('    concurrency:\n      group: rc6-unified-paper-deploy\n'
                      '      cancel-in-progress: false',host_section)
        self.assertNotIn('concurrency:\n  group: rc6-unified-paper-deploy',yaml)
        self.assertIn("local.date().isoformat()=='2026-11-04'",yaml)
        self.assertIn("github.event_name == 'schedule'",yaml)
        self.assertIn('workflow_dispatch:',yaml)
        self.assertGreaterEqual(yaml.count("steps.day_gate.outputs.active == '1'"),4)
        self.assertIn('unshare --mount --propagation private',yaml)
        self.assertIn('mount -o remount,bind,ro',yaml)
        self.assertIn("obj.get('provider_gets')!=0",yaml)
        self.assertNotIn('actions: write',yaml)

    def test_49_utc_slots_and_art_gate_are_not_product_admission(self):
        self.assertEqual(49,len(SLOTS))
        self.assertEqual('10:07',SLOTS[0].astimezone(__import__('zoneinfo').ZoneInfo(
            'America/Argentina/Buenos_Aires')).strftime('%H:%M'))
        self.assertEqual('18:07',SLOTS[-1].astimezone(__import__('zoneinfo').ZoneInfo(
            'America/Argentina/Buenos_Aires')).strftime('%H:%M'))
        self.assertFalse(expected_watch_probe(SLOTS[0]))
        self.assertFalse(expected_watch_probe(SLOTS[1]))
        self.assertFalse(expected_watch_probe(SLOTS[2]))
        self.assertTrue(expected_watch_probe(SLOTS[3]))
        self.assertTrue(expected_watch_probe(SLOTS[-1]))
        self.assertEqual([],slots_for_day(date(2026,11,7)))

    def test_real_schedule_success_is_probed_not_runtime_validated(self):
        target=SLOTS[3]
        result=audit([run(target)],{'100':jobs()})
        row=slot_record(result,target)
        self.assertEqual('PROBE_STEP_SUCCEEDED',row['classification'])
        self.assertEqual('PROBE_STEP_SUCCEEDED_NOT_RUNTIME_PROOF',row['watch_probe'])
        self.assertEqual('NO_VERIFICADO',result['host_eod_t1']['state'])
        self.assertEqual('NO_VERIFICADO',result['host_eod_t1']['dbus_invocation_id'])
        self.assertEqual(0,result['provider_gets'])
        self.assertIs(result['order_routes_called'],False)

    def test_delayed_run_distinct_from_no_trigger(self):
        target=SLOTS[4]
        result=audit([run(target,lag=400)],{'100':jobs()})
        row=slot_record(result,target)
        self.assertEqual('DELAYED',row['classification'])
        self.assertEqual(400,row['creation_delay_seconds'])
        self.assertEqual('PROBE_STEP_SUCCEEDED_NOT_RUNTIME_PROOF',row['watch_probe'])

    def test_queued_start_delay_even_if_creation_was_prompt(self):
        target=SLOTS[4]
        result=audit([run(target,lag=30,start_lag=1000)],{'100':jobs()})
        self.assertEqual('DELAYED',slot_record(result,target)['classification'])

    def test_skipped_and_failed_are_not_fresh_host_probes(self):
        first,second=SLOTS[3:5]
        result=audit([run(first,run_id=101),run(second,run_id=102,conclusion='failure')],
                     {'101':jobs(outcome='skipped',step='skipped'),
                      '102':jobs(outcome='failure',step='failure')})
        self.assertEqual('SKIPPED',slot_record(result,first)['classification'])
        self.assertEqual('FAILED',slot_record(result,second)['classification'])

    def test_offhours_expected_skip_is_distinguished(self):
        target=SLOTS[0]
        result=audit([run(target)],{'100':jobs(outcome='success',step='skipped')})
        row=slot_record(result,target)
        self.assertEqual('EXPECTED_OFF_HOURS_SKIP',row['gate'])
        self.assertEqual('SKIPPED',row['classification'])
        self.assertEqual('EXPECTED_TIME_GATE_SKIP',row['reason'])

    def test_absent_run_classifies_no_trigger_only_after_grace(self):
        target=SLOTS[3]
        early=audit([],as_of=target+timedelta(minutes=18))
        mature=audit([],as_of=target+timedelta(minutes=21))
        self.assertEqual('PENDING_GRACE',slot_record(early,target)['classification'])
        self.assertEqual('NO_TRIGGER',slot_record(mature,target)['classification'])
        self.assertEqual('RUN_ABSENT_FROM_SLOT_WINDOW_NOT_PLATFORM_CAUSE',
                         slot_record(mature,target)['reason'])

    def test_no_api_coverage_must_not_fabricate_no_trigger(self):
        result=audit([],coverage=False)
        self.assertEqual('NO_VERIFICADO',slot_record(result,SLOTS[3])['classification'])
        self.assertEqual('NO_VERIFICADO',result['source_scheduler_finding'])
        self.assertIsNone(result['historical_mature_two_slot_silence_observed'])
        self.assertIsNone(result['latest_mature_two_slot_silence_now'])

    def test_manual_and_push_never_certify_schedule(self):
        target=SLOTS[3]
        result=audit([run(target,event='workflow_dispatch',run_id=101),
                      run(target,event='push',run_id=102)])
        self.assertEqual('NO_TRIGGER',slot_record(result,target)['classification'])
        self.assertEqual([],result['unattributed_genuine_schedule_runs'])

    def test_late_unmatched_run_prevents_definitive_slot_attribution(self):
        target=SLOTS[3]
        # A run after >10m may belong to an earlier GitHub cron; without the
        # event.schedule payload it must remain unassigned and ambiguous.
        very_late=run(target,lag=12*60,run_id=101)
        result=audit([very_late],as_of=target+timedelta(minutes=25))
        self.assertEqual('NO_TRIGGER_OR_LATE_AMBIGUOUS',
                         slot_record(result,target)['classification'])
        self.assertIn(101,slot_record(result,target)['ambiguous_neighbor_run_ids'])
        # The next-slot assignment is only inferred, never causal proof.
        self.assertEqual(101,slot_record(result,SLOTS[4])['run_id'])

    def test_two_mature_active_missing_slots_raise_p1_not_platform_root_cause(self):
        as_of=SLOTS[5]+timedelta(minutes=22)
        result=audit([],as_of=as_of)
        self.assertEqual('P1_SCHEDULE_SILENT',result['source_scheduler_finding'])
        self.assertIs(result['historical_mature_two_slot_silence_observed'],True)
        self.assertIs(result['latest_mature_two_slot_silence_now'],True)

    def test_late_schedule_burst_does_not_erase_42_prior_mature_gaps(self):
        # Reduced, deterministic reproduction of the genuine Nov-04 17:33 ART
        # A627 classification: 42 NO_TRIGGER, 1 ambiguous neighbor,
        # 1 delayed native tick and 1 grace slot. This is NOT runtime evidence.
        target=next(slot for slot in SLOTS if slot.hour == 20 and slot.minute == 17)
        result=audit([run(target,lag=400,run_id=41906177811)],
                     {'41906177811':jobs()},
                     as_of=target+timedelta(minutes=16,seconds=45))
        self.assertEqual({'NO_TRIGGER':42, 'NO_TRIGGER_OR_LATE_AMBIGUOUS':1,
                          'DELAYED':1, 'PENDING_GRACE':1},result['counts'])
        self.assertIs(result['historical_mature_two_slot_silence_observed'],True)
        self.assertIs(result['latest_mature_two_slot_silence_now'],False)
        self.assertEqual('P1_SCHEDULE_SILENT',result['source_scheduler_finding'])
        self.assertEqual('NO_VERIFICADO',result['host_eod_t1']['state'])
        self.assertEqual(0,result['provider_gets'])

    def test_latest_and_historical_windows_both_clean_with_two_native_ticks(self):
        first,second=SLOTS[3:5]
        result=audit([run(first,run_id=101),run(second,run_id=102)],
                     {'101':jobs(),'102':jobs()},
                     as_of=second+timedelta(minutes=21))
        self.assertIs(result['historical_mature_two_slot_silence_observed'],False)
        self.assertIs(result['latest_mature_two_slot_silence_now'],False)
        self.assertEqual('NO_CONFIRMED_TWO_SLOT_SILENCE',
                         result['source_scheduler_finding'])

    def test_missing_jobs_or_rerun_never_prove_host(self):
        target=SLOTS[3]
        result=audit([run(target,attempt=2)],{})
        self.assertEqual('NO_VERIFICADO_RERUN',slot_record(result,target)['classification'])
        self.assertEqual('JOB_EVIDENCE_UNAVAILABLE',slot_record(result,target)['watch_probe'])
        with_success_jobs=audit([run(target,attempt=2)],{'100':jobs()})
        self.assertEqual('NO_VERIFICADO_RERUN',
                         slot_record(with_success_jobs,target)['classification'])

    def test_incomplete_identity_and_unaware_clock_fail_closed(self):
        bad=run(SLOTS[3]);bad['workflow_id']=1
        self.assertFalse(genuine_schedule_run(bad))
        self.assertIsNone(clock('2026-11-04T10:30:00'))
        with self.assertRaises(ValueError):
            classify_cadence([],{},as_of='2026-11-04T10:00:00',day_art=DAY)

    def test_manual_cli_offline_fixture_produces_no_host_or_provider_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            fixture=root/'fixture.json'
            output=root/'receipt.json'
            fixture.write_text(json.dumps({
                'workflow_runs':[run(SLOTS[3])],
                'jobs_by_run_id':{'100':jobs()},
                'coverage_complete':True}),encoding='utf-8')
            self.assertEqual(0,main(['--day-art','2026-11-04',
                '--as-of-utc','2026-11-04T15:00:00Z',
                '--fixture',str(fixture),'--output',str(output)]))
            result=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual('SYNTHETIC_FIXTURE',result['source_status'])
            self.assertEqual('NON_OPERATIONAL_FIXTURE',result['source_scheduler_finding'])
            self.assertEqual('NO_VERIFICADO',result['host_eod_t1']['state'])
            self.assertEqual(0,result['provider_gets'])


if __name__ == '__main__':
    unittest.main()
