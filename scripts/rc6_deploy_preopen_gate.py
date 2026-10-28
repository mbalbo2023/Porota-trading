#!/usr/bin/env python3
"""Validate preopen output without converting NOT_DUE into trading permission.

This is a deployment-result contract, not a replacement for rc6_preopen or its
calendar. No network, database, service or order operation is performed here.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import re
import sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Argentina/Buenos_Aires')
SCHEMA = 'POROTA_RC6_PREOPEN_V3'
PHASES = {'T_MINUS_45', 'T_MINUS_10'}
CHECKS = {
    'release_identity', 'observer_container', 'dashboard_container',
    'observer_db', 'disk', 'required_rc6_timers', 'byma_morning_watch',
    'iol_reference_fallback', 'history_quarantine', 'foreign_market_policy',
}
NOT_DUE_KEYS = {
    'schema', 'generated_at_ar', 'status', 'reason',
    'read_only', 'network_order_test_performed',
}

class PreopenRejected(ValueError):
    pass

def require(condition, message):
    if not condition:
        raise PreopenRejected(message)

def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def invalid_constant(value):
        raise PreopenRejected('NONFINITE_JSON_CONSTANT')
    require(isinstance(text, str) and len(text) <= 1024 * 1024, 'OUTPUT_SIZE')
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PreopenRejected('INVALID_JSON') from exc

def validate_report(report, *, phase, return_code, now, is_operational):
    require(type(return_code) is int and return_code == 0, 'PROCESS_FAILED')
    require(phase in PHASES, 'PHASE_INVALID')
    require(isinstance(report, dict), 'REPORT_NOT_OBJECT')
    require(report.get('schema') == SCHEMA, 'SCHEMA_INVALID')
    require(report.get('read_only') is True, 'READ_ONLY_NOT_TRUE')
    require(report.get('network_order_test_performed') is False, 'ORDER_TEST_NOT_FALSE')
    require(isinstance(now, datetime) and now.tzinfo is not None, 'NOW_NOT_AWARE')
    try:
        observed = datetime.fromisoformat(str(report.get('generated_at_ar', '')).replace('Z', '+00:00'))
    except (ValueError, TypeError) as exc:
        raise PreopenRejected('TIMESTAMP_INVALID') from exc
    require(observed.tzinfo is not None, 'TIMESTAMP_NAIVE')
    local_now = now.astimezone(TZ)
    local_observed = observed.astimezone(TZ)
    require(observed.utcoffset() == local_observed.utcoffset(), 'TIMESTAMP_NOT_ARGENTINA')
    require(local_observed.date() == local_now.date(), 'CALENDAR_DATE_MISMATCH')
    require(0 <= (now - observed).total_seconds() <= 300, 'TIMESTAMP_NOT_FRESH')
    try:
        operational = is_operational(local_observed.date())
    except Exception as exc:
        raise PreopenRejected('CALENDAR_UNAVAILABLE') from exc
    require(type(operational) is bool, 'CALENDAR_RESULT_NOT_BOOL')
    status = report.get('status')
    if status == 'NOT_DUE':
        require(set(report) == NOT_DUE_KEYS, 'NOT_DUE_SHAPE_INVALID')
        require(report.get('reason') == 'BYMA_NON_OPERATIONAL_DAY', 'NOT_DUE_REASON_INVALID')
        require(operational is False, 'NOT_DUE_ON_OPERATIONAL_DAY')
    elif status in {'GREEN','PAUSED_NO_GO'}:
        require(operational is True, 'GREEN_ON_NON_OPERATIONAL_DAY' if status == 'GREEN'
                else 'OPERATOR_PAUSE_ON_NON_OPERATIONAL_DAY')
        require(report.get('phase') == phase, 'PHASE_MISMATCH')
        require(report.get('dry_equivalent') is True, 'DRY_EQUIVALENT_NOT_TRUE')
        require(report.get('direct_telegram_send') is False, 'TELEGRAM_NOT_FALSE')
        require(report.get('red_checks') == [], 'RED_CHECKS_PRESENT')
        checks = report.get('checks')
        require(isinstance(checks, dict) and CHECKS <= set(checks), 'REQUIRED_CHECKS_MISSING')
        require(all(isinstance(item, dict) and item.get('state') in {'GREEN', 'AMBER'}
                    for item in checks.values()), 'CHECK_NOT_ACCEPTABLE')
        timer = checks['required_rc6_timers']
        if status == 'PAUSED_NO_GO':
            # A125 and A151 are separate dated operator decisions. The
            # installed preopen code attests the corresponding immutable
            # receipt and the disabled timer; neither represents PAPER GO.
            day = local_now.date().isoformat()
            require(day in {'2026-10-27', '2026-10-28'},
                    'OPERATOR_PAUSE_DAY_EXPIRED')
            require(report.get('operator_paper_paused') is True and
                    timer.get('operator_paper_paused') is True,
                    'OPERATOR_PAUSE_NOT_ATTESTED')
            operation = report.get('operator_pause_operation_id')
            require(report.get('operator_pause_day_ar') == day and
                    timer.get('operator_pause_day_ar') == day and
                    isinstance(operation, str) and
                    ((day == '2026-10-27' and operation == 'A125-40501523106-1') or
                     (day == '2026-10-28' and re.fullmatch(
                         r'A151-[1-9][0-9]{0,15}-[1-9][0-9]{0,3}', operation))) and
                    timer.get('operator_pause_operation_id') == operation,
                    'OPERATOR_PAUSE_IDENTITY_RED')
            digest = report.get('operator_pause_receipt_sha256')
            require(isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) and
                    timer.get('operator_pause_receipt_sha256') == digest,
                    'OPERATOR_PAUSE_RECEIPT_RED')
        else:
            require(local_now.date().isoformat() not in {'2026-10-27', '2026-10-28'},
                    'GREEN_CANNOT_OVERRIDE_OPERATOR_HOLD')
            require(report.get('operator_paper_paused') is not True and
                    timer.get('operator_paper_paused') is not True,
                    'GREEN_CANNOT_MASK_OPERATOR_PAUSE')
    else:
        raise PreopenRejected('STATUS_NOT_ACCEPTABLE')
    if local_now.date().isoformat() >= '2026-10-29':
        require(report.get('paper_admission_hold') ==
                'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM',
                'PAPER_REARM_HOLD_UNPROVEN')
    return {
        'schema': 'POROTA_DEPLOY_PREOPEN_RESULT_V1', 'accepted': True,
        'preopen_status': status, 'phase': phase,
        'calendar_operational': operational,
        'readiness_verified': status == 'GREEN',
        'operator_paper_paused': status == 'PAUSED_NO_GO',
        'execution_authorized': False,
        'paper_admission_hold': report.get('paper_admission_hold'),
        'generated_at_ar': report['generated_at_ar'],
    }

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=sorted(PHASES), required=True)
    parser.add_argument('--return-code', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        # Use the exact installed RC6 calendar. Import failure is blocking.
        import ak_byma_calendar as calendar
        report = strict_json(sys.stdin.read(1024 * 1024 + 1))
        result = validate_report(report, phase=args.phase, return_code=args.return_code,
                                 now=datetime.now(TZ),
                                 is_operational=calendar.es_dia_habil_operativo)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({'accepted': False, 'execution_authorized': False,
                          'error': type(exc).__name__, 'reason': str(exc)}), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
