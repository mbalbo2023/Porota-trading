#!/usr/bin/env python3
"""A627: GitHub Actions schedule causality, entirely OFFHOST/READ_ONLY.

Never contacts PPI, IOL, a host, SQLite, systemd or any order route. GitHub
workflow run creation does not disclose the intended cron expression/slot;
slot matching is explicitly inferred, not a D-Bus/InvocationID attestation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ART = ZoneInfo('America/Argentina/Buenos_Aires')
UTC = timezone.utc
REPO = 'mbalbo2023/Porota-trading'
WORKFLOW_ID = 384354765
WORKFLOW_PATH = '.github/workflows/rc6-market-watch-readonly.yml'
SCHEMA = 'rc6.a627.schedule-causality-offhost.v1'
MAX_RESPONSE_BYTES = 2_000_000
MAX_RUN_PAGES = 3
MAX_JOBS = 70
GRACE = timedelta(minutes=20)
SLOT_INTERVAL = timedelta(minutes=10)
DELAY_LIMIT = timedelta(minutes=5)
RUN_FIELDS = frozenset({'id', 'event', 'head_branch', 'path', 'workflow_id',
                       'run_attempt', 'status', 'conclusion', 'created_at',
                       'run_started_at'})


def clock(value):
    try:
        if not isinstance(value, str):
            return None
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() is None:
            return None
        return result.astimezone(UTC)
    except (ValueError, OverflowError, TypeError):
        return None


def slots_for_day(day: date):
    """Literal UTC cron of main. Gate is independently expressed in ART."""
    if day.weekday() >= 5:
        return []
    result = []
    for hour in range(13, 21):
        for minute in (7, 17, 27, 37, 47, 57):
            result.append(datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC))
    result.append(datetime(day.year, day.month, day.day, 21, 7, tzinfo=UTC))
    return result


def expected_watch_probe(slot):
    local = slot.astimezone(ART)
    minute = local.hour * 60 + local.minute
    late = date(2026, 11, 2) <= local.date() <= date(2026, 12, 31)
    return local.weekday() < 5 and 10 * 60 + 30 <= minute < (
        18 * 60 + 15 if late else 17 * 60 + 15)


def genuine_schedule_run(run):
    if not isinstance(run, dict):
        return False
    run_id = run.get('id')
    attempt = run.get('run_attempt')
    return (type(run_id) is int and run_id > 0
            and type(attempt) is int and attempt >= 1
            and run.get('event') == 'schedule'
            and run.get('head_branch') == 'main'
            and run.get('path') == WORKFLOW_PATH
            and run.get('workflow_id') == WORKFLOW_ID
            and clock(run.get('created_at')) is not None)


def _watch_job_detail(jobs):
    if not isinstance(jobs, list):
        return 'JOB_EVIDENCE_UNAVAILABLE'
    watch = [j for j in jobs if isinstance(j, dict) and j.get('name') == 'watch']
    if len(watch) != 1:
        return 'JOB_IDENTITY_UNVERIFIED'
    job = watch[0]
    if job.get('conclusion') == 'failure':
        return 'FAILED'
    if job.get('status') != 'completed':
        return 'RUNNING'
    if job.get('conclusion') == 'skipped':
        return 'SKIPPED'
    if job.get('conclusion') != 'success':
        return 'JOB_EVIDENCE_UNAVAILABLE'
    steps = [step for step in job.get('steps', []) if isinstance(step, dict)
             and step.get('name') == 'Collect bounded read-only host truth']
    if len(steps) != 1:
        return 'JOB_EVIDENCE_UNAVAILABLE'
    step = steps[0]
    if step.get('conclusion') == 'success' and step.get('status') == 'completed':
        return 'PROBE_STEP_SUCCEEDED_NOT_RUNTIME_PROOF'
    if step.get('conclusion') == 'skipped':
        return 'SKIPPED'
    if step.get('conclusion') == 'failure':
        return 'FAILED'
    return 'JOB_EVIDENCE_UNAVAILABLE'


def classify_cadence(runs, jobs_by_run, *, as_of, day_art, lookback_days=1,
                     coverage_complete=True):
    """Classify observed Actions metadata, never equate run success to runtime.

    A run is assigned only to the single 10-minute creation window after its
    UTC cron slot. Runs delayed beyond that are reported as UNATTRIBUTED;
    nearby unmatched slots remain ambiguous instead of inventing NO_TRIGGER.
    """
    now = clock(as_of) if isinstance(as_of, str) else as_of
    if (not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() is None or type(lookback_days) is not int
            or not 1 <= lookback_days <= 3 or not isinstance(day_art, date)
            or not isinstance(runs, list) or len(runs) > 300
            or not isinstance(jobs_by_run, dict)
            or type(coverage_complete) is not bool):
        raise ValueError('SCHEDULE_AUDIT_INPUT_UNVERIFIED')
    now = now.astimezone(UTC)
    days = [day_art - timedelta(days=delta)
            for delta in reversed(range(lookback_days))]
    slots = sorted(slot for day in days for slot in slots_for_day(day)
                   if slot <= now)
    clean = sorted((r for r in runs if genuine_schedule_run(r)
                    and clock(r['created_at']) <= now),
                   key=lambda r: (clock(r['created_at']), r['id']))
    assigned = set()
    records = []
    for slot in slots:
        matches = [r for r in clean if r['id'] not in assigned
                   and slot <= clock(r['created_at']) < slot + SLOT_INTERVAL]
        # Multiple trigger IDs in one slot are evidence of ambiguity, not two
        # certified cron ticks. Choose first and surface the excess separately.
        run = matches[0] if matches else None
        if run:
            assigned.add(run['id'])
        due = now >= slot + GRACE
        expects_probe = expected_watch_probe(slot)
        record = {
            'slot_utc': slot.isoformat(),
            'slot_art': slot.astimezone(ART).isoformat(),
            'gate': 'WATCH_EXPECTED' if expects_probe else 'EXPECTED_OFF_HOURS_SKIP',
            'assignment': 'INFERRED_10M_CREATION_WINDOW',
            'run_id': run['id'] if run else None,
            'run_attempt': run['run_attempt'] if run else None,
            'classification': None,
            'watch_probe': 'NO_VERIFICADO',
            'creation_delay_seconds': None,
            'start_delay_seconds': None,
            'ambiguous_neighbor_run_ids': [],
            'reason': None,
        }
        if not run:
            # A run assigned to the *next* cron slot may actually be a >10m
            # late trigger for this one. Without github.event.schedule it
            # cannot establish either intention, even if its ID is assigned.
            later = [r for r in clean if
                     slot + SLOT_INTERVAL <= clock(r['created_at']) < slot + GRACE]
            record['ambiguous_neighbor_run_ids'] = [r['id'] for r in later[:5]]
            record['classification'] = ('PENDING_GRACE' if not due else
                'NO_VERIFICADO' if not coverage_complete else
                'NO_TRIGGER_OR_LATE_AMBIGUOUS' if later else 'NO_TRIGGER')
            record['reason'] = 'RUN_ABSENT_FROM_SLOT_WINDOW_NOT_PLATFORM_CAUSE'
        else:
            created = clock(run['created_at'])
            started = clock(run.get('run_started_at'))
            create_delay = created - slot
            start_delay = (started - slot) if started else None
            record['creation_delay_seconds'] = int(create_delay.total_seconds())
            record['start_delay_seconds'] = int(start_delay.total_seconds()) if start_delay else None
            detail = _watch_job_detail(jobs_by_run.get(str(run['id'])))
            record['watch_probe'] = detail
            if run['run_attempt'] > 1:
                # Latest jobs describe the rerun, not the original scheduled
                # probe. Its existence proves a trigger, not a fresh tick.
                state = 'NO_VERIFICADO_RERUN'
            elif run.get('conclusion') == 'failure' or detail == 'FAILED':
                state = 'FAILED'
            elif detail == 'SKIPPED':
                state = 'SKIPPED'
            elif run.get('status') != 'completed' or detail == 'RUNNING':
                state = 'RUNNING'
            elif detail in {'JOB_EVIDENCE_UNAVAILABLE', 'JOB_IDENTITY_UNVERIFIED'}:
                state = 'NO_VERIFICADO'
            elif create_delay > DELAY_LIMIT or (start_delay and start_delay > DELAY_LIMIT):
                state = 'DELAYED'
            elif detail == 'PROBE_STEP_SUCCEEDED_NOT_RUNTIME_PROOF':
                state = 'PROBE_STEP_SUCCEEDED'
            else:
                state = 'NO_VERIFICADO'
            record['classification'] = state
            if not expects_probe and detail == 'SKIPPED':
                record['reason'] = 'EXPECTED_TIME_GATE_SKIP'
            elif not expects_probe and detail == 'PROBE_STEP_SUCCEEDED_NOT_RUNTIME_PROOF':
                record['reason'] = 'UNEXPECTED_OFFHOURS_PROBE_REVIEW'
            elif run['run_attempt'] > 1:
                record['reason'] = 'RERUN_NOT_ORIGINAL_COMPLETION'
            else:
                record['reason'] = 'TRIGGER_OBSERVED_HOST_NOT_ATTESTED'
        records.append(record)
    extras = [{'run_id':r['id'],'created_at':clock(r['created_at']).isoformat(),
               'reason':'UNATTRIBUTED_GENUINE_SCHEDULE_OR_DUPLICATE'}
              for r in clean if r['id'] not in assigned]
    mature_active = [r for r in records if r['gate']=='WATCH_EXPECTED'
                     and now >= clock(r['slot_utc']) + GRACE]
    last_two = mature_active[-2:]
    silent = (len(last_two)==2 and all(r['classification'] in
              {'NO_TRIGGER','FAILED','SKIPPED'} for r in last_two)
              and coverage_complete)
    counts = {}
    for record in records:
        state = record['classification']
        counts[state] = counts.get(state, 0) + 1
    return {
        'schema':SCHEMA, 'source':'GITHUB_ACTIONS_METADATA_ONLY',
        'source_status':'VERIFIED_API' if coverage_complete else 'NO_VERIFICADO',
        'read_only':True, 'provider_gets':0, 'order_routes_called':False,
        'as_of_utc':now.isoformat(), 'as_of_art':now.astimezone(ART).isoformat(),
        'day_art':day_art.isoformat(), 'lookback_days':lookback_days,
        'due_slots':len(records), 'counts':counts, 'slots':records,
        'unattributed_genuine_schedule_runs':extras,
        'source_scheduler_finding':'P1_SCHEDULE_SILENT' if silent else
            'NO_VERIFICADO' if not coverage_complete else 'NO_CONFIRMED_TWO_SLOT_SILENCE',
        'host_eod_t1':{'state':'NO_VERIFICADO','dbus_invocation_id':'NO_VERIFICADO',
             'timer_service_result':'NO_VERIFICADO',
             'reason':'NO_AUTHENTIC_HOST_INVOCATION_RECEIPT_IN_THIS_OFFHOST_AUDIT',
             'timer_references_art':{'preopen':['09:45','10:10','10:20'],
                                     'postclose':['17:08','18:08','18:45',
                                                  '18:20-22:50 EOD source windows']}},
    }


def _github_json(path, token):
    if not path.startswith('/repos/'+REPO+'/actions/'):
        raise ValueError('GITHUB_API_SCOPE_INVALID')
    headers = {'Accept':'application/vnd.github+json',
               'User-Agent':'porota-rc6-a627-schedule-causality/1'}
    if token:
        headers['Authorization']='Bearer '+token
    with urlopen(Request('https://api.github.com'+path, headers=headers),timeout=15) as response:
        raw=response.read(MAX_RESPONSE_BYTES+1)
    if len(raw)>MAX_RESPONSE_BYTES:
        raise ValueError('GITHUB_API_RESPONSE_OVERSIZE')
    data=json.loads(raw)
    if not isinstance(data,dict):
        raise ValueError('GITHUB_API_RESPONSE_INVALID')
    return data


def fetch_github_evidence(day_art, lookback_days, *, token):
    first = day_art-timedelta(days=lookback_days-1)
    start = datetime(first.year,first.month,first.day,tzinfo=ART).astimezone(UTC)
    all_runs=[]; page_complete=False
    for page in range(1,MAX_RUN_PAGES+1):
        payload=_github_json(f'/repos/{REPO}/actions/workflows/{WORKFLOW_ID}/runs'
            f'?event=schedule&per_page=100&page={page}', token)
        rows=payload.get('workflow_runs')
        if not isinstance(rows,list) or len(rows)>100:
            raise ValueError('GITHUB_RUNS_SHAPE_INVALID')
        all_runs.extend(rows)
        if not rows or len(rows)<100:
            page_complete=True
            break
        timestamps=[clock(r.get('created_at')) for r in rows if isinstance(r,dict)]
        if timestamps and all(t is not None for t in timestamps) and min(timestamps)<start:
            page_complete=True
            break
    # A single page of fewer than 100 runs confirms pagination end; otherwise
    # an old-enough run confirms the historical horizon was covered.
    if not page_complete:
        return all_runs, {}, False
    relevant=[r for r in all_runs if genuine_schedule_run(r) and
              start <= clock(r['created_at']) <= datetime.now(UTC)]
    jobs={}
    for run in relevant[:MAX_JOBS]:
        try:
            value=_github_json(f'/repos/{REPO}/actions/runs/{run["id"]}/jobs?per_page=100',token)
            rows=value.get('jobs')
            jobs[str(run['id'])]=rows if isinstance(rows,list) and len(rows)<=100 else None
        except (OSError,ValueError,TimeoutError,KeyError):
            jobs[str(run['id'])]=None
    return all_runs, jobs, True


def main(argv=None):
    parser=argparse.ArgumentParser(description='OFFHOST GitHub schedule causal receipts')
    parser.add_argument('--day-art',default=datetime.now(UTC).astimezone(ART).date().isoformat())
    parser.add_argument('--lookback-days',type=int,default=1,choices=(1,2,3))
    parser.add_argument('--as-of-utc',default=None)
    parser.add_argument('--fixture',default=None)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    path=Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        day=date.fromisoformat(args.day_art)
        as_of=clock(args.as_of_utc) if args.as_of_utc else datetime.now(UTC)
        if as_of is None or (not args.fixture and as_of>datetime.now(UTC)+timedelta(minutes=1)):
            raise ValueError('FUTURE_OR_UNAWARE_AS_OF')
        if args.fixture:
            data=json.loads(Path(args.fixture).read_text(encoding='utf-8'))
            runs=data['workflow_runs']; jobs=data['jobs_by_run_id']
            coverage=data.get('coverage_complete') is True
        else:
            runs,jobs,coverage=fetch_github_evidence(
                day,args.lookback_days,token=os.getenv('GITHUB_TOKEN',''))
        result=classify_cadence(runs,jobs,as_of=as_of,day_art=day,
                                lookback_days=args.lookback_days,
                                coverage_complete=coverage)
        if args.fixture:
            # A local fixture can test classification, not certify GitHub
            # historical receipt or an operational P1 incident.
            result['source_status']='SYNTHETIC_FIXTURE'
            result['source_scheduler_finding']='NON_OPERATIONAL_FIXTURE'
    except (OSError,ValueError,TypeError,KeyError,OverflowError) as exc:
        result={'schema':SCHEMA,'source':'GITHUB_ACTIONS_METADATA_ONLY',
                'source_status':'NO_VERIFICADO','state':'NO_VERIFICADO',
                'reason':type(exc).__name__,'read_only':True,'provider_gets':0,
                'order_routes_called':False,'slots':[],
                'host_eod_t1':{'state':'NO_VERIFICADO',
                                'dbus_invocation_id':'NO_VERIFICADO'}}
    path.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print('A627_GITHUB_SCHEDULE_SOURCE='+result['source_status'])
    print('A627_SCHEDULE_FINDING='+result.get('source_scheduler_finding','NO_VERIFICADO'))
    print('A627_DUE_SLOTS='+str(result.get('due_slots','NO_VERIFICADO')))
    return 0 if result['source_status'] in {'VERIFIED_API','SYNTHETIC_FIXTURE'} else 2


if __name__=='__main__':
    raise SystemExit(main())
