"""A657 OFFHOST gate tests: stale GitHub schedule never reaches A617 host SSH.

The function under test is extracted from the *actual workflow* here-doc,
avoiding a synthetic copy of the production gate. No provider or host I/O.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import textwrap
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/rc6-a617-ppi-origin-selector-readonly.yml'
CRONS = {
    '05 14 4 11 *': (11, 5),
    '50 17 4 11 *': (14, 50),
    '50 19 4 11 *': (16, 50),
}
ART = ZoneInfo('America/Argentina/Buenos_Aires')


def load_actual_gate():
    workflow = WORKFLOW.read_text(encoding='utf-8')
    marker = '      - name: Gate exact 04-Nov ART schedule slot, or explicit manual fallback\n'
    if workflow.count(marker) != 1:
        raise AssertionError('A657_A617_GATE_STEP_IDENTITY_UNVERIFIED')
    block = workflow.split(marker, 1)[1].split('\n      - name:', 1)[0]
    match = re.search(r"^          python3 - <<'PY'\n(?P<body>.*?)^          PY$",
                      block, flags=re.M | re.S)
    if match is None:
        raise AssertionError('A657_A617_HEREDOC_UNVERIFIED')
    module = ast.parse(textwrap.dedent(match.group('body')))
    functions = [node for node in module.body if isinstance(node, ast.FunctionDef)
                 and node.name == 'classify_a617_gate']
    if len(functions) != 1:
        raise AssertionError('A657_A617_PURE_GATE_UNVERIFIED')
    fn = ast.Module(body=functions, type_ignores=[])
    context = {'ZoneInfo': ZoneInfo}
    exec(compile(fn, str(WORKFLOW), 'exec'), context)
    return context['classify_a617_gate'], workflow


def observed(day, hour, minute, second=0):
    return datetime(day[0], day[1], day[2], hour, minute, second,
                    tzinfo=ART).astimezone(timezone.utc)


class TestA617BoundedNativeSchedule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gate, cls.yaml = load_actual_gate()
        cls.gate = staticmethod(gate)

    def test_real_19_53_art_arrival_skips_all_three_possible_native_crons(self):
        # Run 41929191137, 04-Nov 22:53:41Z, artifact 13370068029:
        # newest intended trigger was 16:50 ART; inferred lateness >3h.
        now = datetime(2026, 11, 4, 22, 53, 41, tzinfo=timezone.utc)
        for cron in CRONS:
            with self.subTest(cron=cron):
                result = self.gate('schedule', cron, now)
                self.assertFalse(result['active'])
                self.assertEqual('SCHEDULE_STALE_SKIP', result['gate'])
                self.assertGreater(result['delay_seconds'], 20 * 60)

    def test_all_three_exact_observation_slots_accept_bounded_jitter(self):
        for cron, (hour, minute) in CRONS.items():
            instant = observed((2026, 11, 4), hour, minute)
            for seconds in (0, 1, 17 * 60, 20 * 60):
                with self.subTest(cron=cron, seconds=seconds):
                    result = self.gate('schedule', cron,
                                       instant + timedelta(seconds=seconds))
                    self.assertTrue(result['active'])
                    self.assertEqual('SCHEDULE_IN_WINDOW', result['gate'])
                    self.assertEqual(seconds, result['delay_seconds'])

    def test_before_slot_and_one_second_after_grace_are_denied(self):
        for cron, (hour, minute) in CRONS.items():
            instant = observed((2026, 11, 4), hour, minute)
            for delta in (-1, 20 * 60 + 1):
                with self.subTest(cron=cron, seconds=delta):
                    result = self.gate('schedule', cron,
                                       instant + timedelta(seconds=delta))
                    self.assertFalse(result['active'])
                    self.assertEqual('SCHEDULE_STALE_SKIP', result['gate'])

    def test_wrong_year_unknown_cron_and_unexpected_event_fail_closed(self):
        now = observed((2026, 11, 4), 16, 50)
        bad = [
            ('schedule', '50 19 4 11 *', observed((2027, 11, 4), 16, 50),
             'SCHEDULE_WRONG_DAY_SKIP'),
            ('schedule', '50 19 4 11 * ', now, 'SCHEDULE_UNKNOWN_CRON_SKIP'),
            ('schedule', None, now, 'SCHEDULE_UNKNOWN_CRON_SKIP'),
            ('push', '50 19 4 11 *', now, 'UNEXPECTED_EVENT_SKIP'),
        ]
        for event, cron, instant, expected in bad:
            with self.subTest(event=event, cron=cron):
                result = self.gate(event, cron, instant)
                self.assertFalse(result['active'])
                self.assertEqual(expected, result['gate'])

    def test_manual_action_is_labeled_fallback_not_native_freshness(self):
        now = datetime(2026, 11, 4, 22, 53, 41, tzinfo=timezone.utc)
        result = self.gate('workflow_dispatch', None, now)
        self.assertTrue(result['active'])
        self.assertEqual('MANUAL_READ_ONLY_FALLBACK', result['gate'])
        self.assertIsNone(result['scheduled_slot_art'])
        self.assertIsNone(result['delay_seconds'])

    def test_workflow_publishes_skipped_receipt_without_host_or_provider(self):
        workflow = self.yaml
        crons = re.findall(r"^\s+- cron: '([^']+)'", workflow, flags=re.M)
        self.assertEqual(set(CRONS), set(crons))
        self.assertIn('SCHEDULE_CRON: ${{ github.event.schedule }}', workflow)
        self.assertIn('A617_04NOV_HOST_GATE=', workflow)
        self.assertIn("Path('evidence/a617_schedule_gate.json').write_text(", workflow)
        self.assertIn("'host_probe_state':'NOT_ATTESTED_BY_GATE'", workflow)
        self.assertIn("'provider_gets_by_gate':0", workflow)
        self.assertIn("'ppi_watch_touched_by_gate':False", workflow)
        self.assertIn("if: always() && steps.day_gate.outcome == 'success' && ", workflow)
        self.assertIn("steps.day_gate.outputs.active == '0'", workflow)
        self.assertIn('evidence/a617_schedule_gate.json', workflow)
        self.assertIn('evidence/a617_ppi_origin_selector.json', workflow)
        self.assertIn("if: steps.day_gate.outputs.active == '1'", workflow)
        # The guard does not create a scheduled fallback or alter PPI Watch.
        self.assertNotIn('actions: write', workflow)
        self.assertNotIn('ppi_watch.service', workflow.lower())


if __name__ == '__main__':
    unittest.main()
