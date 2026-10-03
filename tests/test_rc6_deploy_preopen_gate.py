"""Offline regression of the 2026-10-03 NOT_DUE deploy failure."""
from datetime import datetime, timedelta
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rc6_deploy_preopen_gate as gate

NOW = datetime(2026, 10, 3, 11, 5, 0, tzinfo=gate.TZ)

def not_due():
    return dict(schema=gate.SCHEMA, generated_at_ar=NOW.isoformat(), status='NOT_DUE',
                reason='BYMA_NON_OPERATIONAL_DAY', read_only=True, network_order_test_performed=False)

def green():
    value = not_due()
    value.pop('reason')
    value.update(status='GREEN', phase='T_MINUS_45', dry_equivalent=True,
                 direct_telegram_send=False, red_checks=[],
                 checks={name: {'state': 'GREEN'} for name in gate.CHECKS})
    return value

class PreopenResultTests(unittest.TestCase):
    def validate(self, report, **kw):
        args = dict(phase='T_MINUS_45', return_code=0, now=NOW, is_operational=lambda day: False)
        args.update(kw)
        return gate.validate_report(report, **args)

    def test_exact_saturday_output_is_accepted_but_never_readiness_or_permission(self):
        for phase in sorted(gate.PHASES):
            result = self.validate(not_due(), phase=phase)
            self.assertTrue(result['accepted'])
            self.assertEqual(result['preopen_status'], 'NOT_DUE')
            self.assertFalse(result['readiness_verified'])
            self.assertFalse(result['execution_authorized'])

    def test_operational_day_cannot_be_skipped(self):
        with self.assertRaisesRegex(gate.PreopenRejected, 'NOT_DUE_ON_OPERATIONAL_DAY'):
            self.validate(not_due(), is_operational=lambda day: True)

    def test_calendar_exception_is_blocking(self):
        def unavailable(day):
            raise RuntimeError('calendar failed')
        with self.assertRaisesRegex(gate.PreopenRejected, 'CALENDAR_UNAVAILABLE'):
            self.validate(not_due(), is_operational=unavailable)

    def test_non_boolean_calendar_is_blocking(self):
        for value in (None, 0, 1, '', 'False'):
            with self.assertRaises(gate.PreopenRejected):
                self.validate(not_due(), is_operational=lambda day, v=value: v)

    def test_unknown_reason_or_extra_hidden_checks_are_rejected(self):
        for field, value in [('reason', 'OUTSIDE_SESSION'), ('checks', {'disk': {'state': 'RED'}})]:
            report = not_due(); report[field] = value
            with self.assertRaises(gate.PreopenRejected):
                self.validate(report)

    def test_nonzero_process_exit_always_blocks(self):
        for value in (1, 2, 124, 137, False, '0'):
            with self.assertRaises(gate.PreopenRejected):
                self.validate(not_due(), return_code=value)

    def test_flags_require_literal_booleans(self):
        for field, values in [('read_only', [False, 1, 'true', None]),
                              ('network_order_test_performed', [True, 0, 'false', None])]:
            for value in values:
                report = not_due(); report[field] = value
                with self.assertRaises(gate.PreopenRejected):
                    self.validate(report)

    def test_stale_future_naive_wrong_day_or_timezone_are_rejected(self):
        stamps = [(NOW-timedelta(seconds=301)).isoformat(), (NOW+timedelta(seconds=1)).isoformat(),
                  (NOW-timedelta(days=1)).isoformat(), '2026-10-03T11:05:00',
                  '2026-10-03T14:05:00+00:00', 'invalid']
        for stamp in stamps:
            report = not_due(); report['generated_at_ar'] = stamp
            with self.assertRaises(gate.PreopenRejected):
                self.validate(report)

    def test_green_still_requires_complete_real_preopen_checks(self):
        report = green()
        report['checks']['disk']['state'] = 'AMBER'
        result = self.validate(report, is_operational=lambda day: True)
        self.assertTrue(result['readiness_verified'])
        self.assertFalse(result['execution_authorized'])

    def test_green_cannot_impersonate_weekend_readiness(self):
        with self.assertRaisesRegex(gate.PreopenRejected, 'GREEN_ON_NON_OPERATIONAL_DAY'):
            self.validate(green())

    def test_green_wrong_phase_missing_or_red_checks_rejected(self):
        variants = []
        value = green(); value['phase'] = 'T_MINUS_10'; variants.append(value)
        value = green(); del value['checks']['observer_db']; variants.append(value)
        value = green(); value['checks']['observer_db']['state'] = 'RED'; variants.append(value)
        value = green(); value['red_checks'] = ['observer_db']; variants.append(value)
        value = green(); value['dry_equivalent'] = False; variants.append(value)
        value = green(); value['direct_telegram_send'] = True; variants.append(value)
        for value in variants:
            with self.assertRaises(gate.PreopenRejected):
                self.validate(value, is_operational=lambda day: True)

    def test_unknown_status_schema_and_nonobject_are_blocking(self):
        for value in [[], None, dict(not_due(), status='RED'), dict(not_due(), status='AMBER'),
                      dict(not_due(), schema='legacy')]:
            with self.assertRaises(gate.PreopenRejected):
                self.validate(value)

    def test_json_is_strict_and_no_input_mutation(self):
        for text in ['{"status":"RED","status":"NOT_DUE"}', '{"x":NaN}', '{} trailing']:
            with self.assertRaises(gate.PreopenRejected):
                gate.strict_json(text)
        report = not_due(); before = copy.deepcopy(report)
        self.validate(gate.strict_json(json.dumps(report)))
        self.assertEqual(report, before)

if __name__ == '__main__':
    unittest.main()
