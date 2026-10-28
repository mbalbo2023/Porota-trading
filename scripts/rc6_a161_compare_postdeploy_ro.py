#!/usr/bin/env python3
"""Runner-only fail-closed comparison of two bounded host receipts."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

PREFIX='RC6_A161_POSTDEPLOY_RO='
ART=ZoneInfo('America/Argentina/Buenos_Aires')
ADMIN_PREFIX='RC6_A163_UNIT_CLOSURE='


def read(path):
    lines=Path(path).read_text(encoding='utf-8').splitlines()
    if len(lines)!=1 or not lines[0].startswith(PREFIX) or len(lines[0])>32768:
        raise ValueError('PULSE_LINE_RED')
    value=json.loads(lines[0][len(PREFIX):])
    if type(value) is not dict or value.get('schema')!='rc6.a161.postdeploy-ro.v1':
        raise ValueError('PULSE_SCHEMA_RED')
    return value


def read_admin(path):
    lines=Path(path).read_text(encoding='utf-8').splitlines()
    if len(lines)!=1 or not lines[0].startswith(ADMIN_PREFIX) or len(lines[0])>8192:
        raise ValueError('ADMIN_RECEIPT_LINE_RED')
    value=json.loads(lines[0][len(ADMIN_PREFIX):])
    if type(value) is not dict:raise ValueError('ADMIN_RECEIPT_OBJECT_RED')
    return value


def date(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('PULSE_CLOCK_RED')
    return d.astimezone(timezone.utc)


def fresh(value,maximum):
    return type(value) in {int,float} and 0<=value<=maximum


def image_id(value):
    return isinstance(value,str) and re.fullmatch(r'sha256:[0-9a-f]{64}',value) is not None


def digest(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def compare(first,second,candidate,tree,deploy,phase='OPEN'):
    if phase not in {'OPEN','CLOSED'}:
        raise ValueError('AUDIT_PHASE_RED')
    for index,pulse in enumerate((first,second),1):
        expected_status='OPEN_PROVISIONAL' if phase=='OPEN' else 'CLOSED_PROVISIONAL'
        if (pulse.get('status')!=expected_status or pulse.get('audit_phase')!=phase or
                pulse.get('candidate_sha')!=candidate or
                pulse.get('candidate_tree')!=tree or pulse.get('deploy_sha')!=deploy or
                pulse.get('droplet')!={'id':'594077619','region':'nyc1'} or
                pulse.get('host_mutations')!=0 or
                pulse.get('iol_provider_requests')!=0 or
                pulse.get('real_orders_sent')!=0 or
                pulse.get('operational_go') is not False or
                pulse.get('release',{}).get('validation')!='VALIDATED_RUNTIME' or
                not image_id(pulse.get('release',{}).get('image_id')) or
                not image_id(pulse.get('release',{}).get('frozen_preload_image_id')) or
                not digest(pulse.get('release',{}).get('frozen_manifest_sha256')) or
                pulse.get('critical')!='ISSUES_ONLY_HEALTHY' or
                pulse.get('dashboard_http')!=200 or
                pulse.get('dashboard',{}).get('mode')!='PRODUCTION_PAPER' or
                pulse.get('dashboard',{}).get('execution')!='SIMULATED' or
                type(pulse.get('dashboard',{}).get('real_orders_sent')) is not int or
                pulse['dashboard']['real_orders_sent']!=0 or
                pulse.get('paper_interlock',{}).get('allow_new_openings') is not False or
                pulse.get('paper_interlock',{}).get('reason') not in {
                    'OPERATOR_PAPER_PAUSE_2026_10_28',
                    'OPERATOR_NO_GO_PENDING_EXPLICIT_REARM'} or
                pulse.get('timers',{}).get('count')!=16 or
                pulse.get('timers',{}).get('active_enabled')!=15 or
                pulse.get('timers',{}).get('operator_preopen_disabled') is not True or
                pulse.get('timers',{}).get('iol_guard_active') is not True or
                pulse.get('paper',{}).get('mode')!='PRODUCTION_PAPER' or
                pulse.get('paper',{}).get('flat_books')!=3 or
                pulse.get('paper',{}).get('real_orders_sent')!=0 or
                pulse.get('paper',{}).get('observer_state')!=
                    ('RUNNING' if phase=='OPEN' else 'WAITING_MARKET') or
                pulse.get('paper',{}).get('ppi_auth')!='OK' or
                pulse.get('shadow',{}).get('phase')!=phase or
                pulse.get('shadow',{}).get('digest_verified') is not True or
                not fresh(pulse.get('iol',{}).get('guardian_age_s'),1900) or
                pulse.get('disk_free_bytes',0)<2*1024**3 or
                pulse.get('disk_inode_free_percent',0)<
                    pulse.get('disk_inode_floor_percent',100)):
            raise ValueError('PULSE_'+str(index)+'_REQUIRED_GATE_RED')
        timers=pulse['timers'];paper=pulse['paper'];iol=pulse['iol']
        if (paper.get('scalping',{}).get('state')!=
                ('RUNNING' if phase=='OPEN' else 'WAITING_MARKET') or
                paper.get('caucion',{}).get('state') not in {
                    'WAITING_WINDOW','WAITING_CALENDAR','HOLD','PLACED_SIMULATED'} or
                paper.get('exit_supervisor',{}).get('state')!='RUNNING' or
                paper.get('exit_reader',{}).get('state')!=
                    ('READY' if phase=='OPEN' else 'WAITING_MARKET') or
                not fresh(paper.get('exit_supervisor',{}).get('heartbeat_age_s'),20) or
                not fresh(paper.get('exit_reader',{}).get('heartbeat_age_s'),20) or
                sum(paper.get('readiness_by_family',{}).values())!=
                    pulse.get('dashboard',{}).get('ready')):
            raise ValueError('PAPER_WORKERS_OR_DASHBOARD_RED')
        if phase=='OPEN':
            if (timers.get('all_healthy') is not False or
                    timers.get('historical_eod_a162_only') is not True or
                    timers.get('historical_cause_run_id')!=40722096423 or
                    timers.get('historical_cause_artifact_id')!=12830702310 or
                    timers.get('historical_cause_artifact_sha256')!=
                    '7f0d8950527fbaef8fd6a0e2ce619c16a3efee385fa98a4f4e4808d329379f83' or
                    not isinstance(timers.get('historical_eod_invocation_id'),str) or
                    not re.fullmatch('[0-9a-f]{32}',timers['historical_eod_invocation_id']) or
                    date(pulse['at_utc']).astimezone(ART).date().isoformat()!='2026-10-28' or
                    not 10*60+30<=date(pulse['at_utc']).astimezone(ART).hour*60+
                        date(pulse['at_utc']).astimezone(ART).minute<17*60 or
                    date(timers.get('eod_next_elapse_utc')).astimezone(ART).date()!=
                        date(pulse['at_utc']).astimezone(ART).date() or
                    date(timers['eod_next_elapse_utc']).astimezone(ART).hour*60+
                        date(timers['eod_next_elapse_utc']).astimezone(ART).minute<17*60+15 or
                    timers.get('failed_units')!=['porota-local-eod-history-rc6.timer'] or
                    paper.get('session_state')!='MARKET_OPEN' or
                    iol.get('cache_state')!='LIVE_FRESH' or
                    not fresh(iol.get('cache_observed_age_s'),240) or
                    not fresh(iol.get('cache_refreshed_age_s'),240)):
                raise ValueError('OPEN_PROVISIONAL_EXACT_HISTORICAL_EOD_RED')
        else:
            if (timers.get('all_healthy') is not True or
                    timers.get('failed_units')!=[] or
                    paper.get('session_state') not in {'CLOSED','MARKET_CLOSED'} or
                    iol.get('cache_state') not in {'LIVE_FRESH','CACHE_FRESH','CACHE_STALE'} or
                    not fresh(iol.get('cache_observed_age_s'),7*86400) or
                    not fresh(iol.get('cache_refreshed_age_s'),7*86400) or
                    pulse['shadow'].get('status') not in {
                        'SHADOW_IDLE_MARKET_CLOSED','SHADOW_CLOSED_ADVANCING'}):
                raise ValueError('CLOSED_EOD_AND_IDLE_GATE_RED')
            shadow=pulse['shadow']
            if shadow['status']=='SHADOW_IDLE_MARKET_CLOSED':
                if (shadow.get('source_freshness_renewed') is not False or
                        not fresh(shadow.get('idle_heartbeat_age_s'),90)):
                    raise ValueError('CLOSED_IDLE_RECEIPT_RED')
            elif not fresh(shadow.get('age_s'),180):
                raise ValueError('CLOSED_ADVANCING_CUT_STALE')
    gap=(date(second['at_utc'])-date(first['at_utc'])).total_seconds()
    if not 190<=gap<=900:
        raise ValueError('PULSES_NOT_190S_APART')
    if first['paper'].get('integrity')!='PRAGMA_QUICK_CHECK_OK' or (
            second['paper'].get('integrity')!='IN_FIRST_PULSE'):
        raise ValueError('DB_INTEGRITY_RECEIPT_RED')
    # PPI Watch has a separate owner and may legitimately change while our
    # strictly read-only observation runs. The receipt never claims causality.
    if (first['release']!=second['release'] or
            first['timers']!=second['timers']):
        raise ValueError('RUNTIME_CONTROL_DRIFT_RED')
    if set(first['containers'])!=set(second['containers']):
        raise ValueError('RUNTIME_CONTAINER_SET_RED')
    for name,prior in first['containers'].items():
        after=second['containers'][name]
        if (prior['id_sha256']!=after['id_sha256'] or
                prior['restarts']!=after['restarts'] or
                after['running'] is not True or after['oom_killed'] is not False):
            raise ValueError('RUNTIME_CONTAINER_RESTART_OR_REPLACED')
    before=first['runtime']['children'];after=second['runtime']['children']
    if set(before)!=set(after) or 'scanner' not in before:
        raise ValueError('RUNTIME_CHILD_SET_RED')
    for name,prior in before.items():
        current=after[name]
        if (prior['running'] is not True or current['running'] is not True or
                prior['pid_present'] is not True or current['pid_present'] is not True or
                prior['restarts']!=current['restarts']):
            raise ValueError('CHILD_RESTART_OR_HEALTH_RED')
    shadow_a,shadow_b=first['shadow'],second['shadow']
    if phase=='OPEN':
        if (shadow_b['generation_id']==shadow_a['generation_id'] or
                shadow_b['sequence']<=shadow_a['sequence'] or
                date(shadow_b['as_of'])<=date(shadow_a['as_of'])):
            raise ValueError('SHADOW_GENERATION_NOT_ADVANCED')
    elif shadow_a['status']!=shadow_b['status']:
        raise ValueError('SHADOW_CLOSED_ROUTE_TRANSITION_REOBSERVE')
    elif shadow_a['status']=='SHADOW_IDLE_MARKET_CLOSED':
        if (shadow_b['generation_id']!=shadow_a['generation_id'] or
                shadow_b['sequence']!=shadow_a['sequence'] or
                shadow_b['source_as_of']!=shadow_a['source_as_of'] or
                date(shadow_b['idle_heartbeat_at'])<=date(shadow_a['idle_heartbeat_at'])):
            raise ValueError('SHADOW_CLOSED_IDLE_NOT_ADVANCED')
    elif (shadow_b['generation_id']==shadow_a['generation_id'] or
            shadow_b['sequence']<=shadow_a['sequence'] or
            date(shadow_b['as_of'])<=date(shadow_a['as_of'])):
        raise ValueError('SHADOW_CLOSED_CUT_NOT_ADVANCED')
    for path in (('paper','scalping','heartbeat_at'),
                 ('paper','caucion','heartbeat_at'),
                 ('paper','exit_supervisor','heartbeat_at'),
                 ('paper','exit_reader','heartbeat_at'),
                 ('paper','observer_heartbeat_at')):
        prior=first
        current=second
        for key in path:
            prior=prior[key];current=current[key]
        if date(current)<=date(prior):
            raise ValueError('WORKER_HEARTBEAT_NOT_ADVANCED')
    if first['paper']['scalping']['failed_cumulative']!=0 or (
            second['paper']['scalping']['failed_cumulative']!=0):
        raise ValueError('SCALPING_FAILED_COUNTER_RED')
    return {'schema':'rc6.a161.two-pulse-acceptance.v2','status':'PROVISIONAL',
            'phase':phase,
            'at_first_utc':first['at_utc'],'at_second_utc':second['at_utc'],
            'separation_seconds':round(gap,3),'candidate_sha':candidate,
            'candidate_tree':tree,'deploy_sha':deploy,
            'scanner_running_without_restart':True,
            'native_shadow_evidence':('ADVANCING_OPEN_GENERATIONS' if phase=='OPEN'
                                      else 'ADVANCING_CLOSED_IDLE_HEARTBEAT' if
                                      shadow_a['status']=='SHADOW_IDLE_MARKET_CLOSED'
                                      else 'ADVANCING_CLOSED_CUTS'),
            'scalping_and_caucion_heartbeat_advancing':True,
            'iol_guard_and_cache_contract_both':True,
            'owned_timer_state_unchanged':True,'ppi_watch_read_only':True,
            'db_quick_check':'OK','real_orders_sent':0,
            'operational_go':False}


def compose(open_first,open_second,closed_first,closed_second,
            preopen_closure,eod_closure,candidate,tree,deploy):
    opened=compare(open_first,open_second,candidate,tree,deploy,'OPEN')
    closed=compare(closed_first,closed_second,candidate,tree,deploy,'CLOSED')
    if not date(open_second['at_utc'])<date(closed_first['at_utc']):
        raise ValueError('COMPOSITE_PHASE_ORDER_RED')
    for phase,closure in (('PREOPEN',preopen_closure),('EOD',eod_closure)):
        if (closure.get('schema')!='rc6.a163.unit-closure.v1' or
                closure.get('phase')!=phase or closure.get('status')!='GREEN' or
                closure.get('candidate_sha')!=candidate or
                closure.get('candidate_tree')!=tree or
                closure.get('deploy_sha')!=deploy or
                closure.get('frozen_manifest_sha256')!=
                    open_first['release'].get('frozen_manifest_sha256') or
                closure.get('frozen_preload_image_id')!=
                    open_first['release'].get('frozen_preload_image_id') or
                closure.get('droplet')!={'id':'594077619','region':'nyc1'} or
                closure.get('operational_go') is not False or
                closure.get('real_orders_sent')!=0 or
                closure.get('ppi_watch_accessed') is not False or
                closure.get('host_mutations')!=(1 if phase=='PREOPEN' else 0) or
                closure.get('forensics',{}).get('refined_run')!=40722096423 or
                closure.get('forensics',{}).get('refined_artifact_id')!=12830702310 or
                closure.get('forensics',{}).get('refined_artifact_sha256')!=
                '7f0d8950527fbaef8fd6a0e2ce619c16a3efee385fa98a4f4e4808d329379f83'):
            raise ValueError('COMPOSITE_ADMIN_CLOSURE_RED')
    if (not date(preopen_closure['at_utc'])<=date(open_first['at_utc']) or
            not date(open_second['at_utc'])<date(eod_closure['at_utc'])<=
                date(closed_first['at_utc']) or
            preopen_closure.get('closure')!='HISTORICAL_FAILURE_DOCUMENTED_AND_RESET_ONE_UNIT' or
            eod_closure.get('closure')!='NEW_SUCCESSFUL_SCHEDULED_EOD_PROVEN' or
            eod_closure.get('eod',{}).get('state')!='LOCAL_EOD_READY' or
            eod_closure['eod'].get('durable_cursor') is not True):
        raise ValueError('COMPOSITE_CAUSAL_ORDER_OR_EOD_RED')
    prior,after=open_second,closed_first
    if (date(prior['release'].get('validated_at_utc'))<=
            date('2026-10-27T22:10:13+00:00') or
            after['shadow']['sequence']<prior['shadow']['sequence'] or
            date(after['shadow'].get('source_as_of') or after['shadow'].get('as_of'))<=
                date(prior['shadow']['as_of'])):
        raise ValueError('COMPOSITE_SHADOW_POSTOPEN_SOURCE_RED')
    if (prior['release']!=after['release'] or
            set(prior['containers'])!=set(after['containers']) or
            set(prior['runtime']['children'])!=set(after['runtime']['children'])):
        raise ValueError('COMPOSITE_RUNTIME_IDENTITY_DRIFT')
    for name,before in prior['containers'].items():
        current=after['containers'][name]
        if (before['id_sha256']!=current['id_sha256'] or
                before['restarts']!=current['restarts']):
            raise ValueError('COMPOSITE_CONTAINER_RESTART')
    for name,before in prior['runtime']['children'].items():
        if before['restarts']!=after['runtime']['children'][name]['restarts']:
            raise ValueError('COMPOSITE_CHILD_RESTART')
    for name,before in prior['timers']['states'].items():
        current=after['timers']['states'].get(name)
        if current is None or any(before[key]!=current[key]
                                  for key in ('state','enabled','migration_expected')):
            raise ValueError('COMPOSITE_TIMER_DRIFT')
    if (prior['timers']['states']['porota-local-eod-history-rc6.timer'][
            'service_result']!='EXIT_CODE' or
            after['timers']['states']['porota-local-eod-history-rc6.timer'][
                'service_result']!='SUCCESS'):
        raise ValueError('COMPOSITE_EOD_SERVICE_TRANSITION_RED')
    if (eod_closure['eod'].get('invocation_id')==
            prior['timers']['historical_eod_invocation_id'] or
            eod_closure['eod'].get('hot_highwater_equals_cursor') is not True or
            date(eod_closure['eod'].get('timer_last_trigger_utc'))<
                date(prior['timers'].get('eod_next_elapse_utc'))):
        raise ValueError('COMPOSITE_NEW_SCHEDULED_EOD_RED')
    dates={date(row['at_utc']).astimezone(ART).date() for row in
           (open_first,open_second,closed_first,closed_second,eod_closure)}
    if dates!={datetime(2026,10,28).date()} or any(
            date(row['at_utc']).astimezone(ART).hour<18
            for row in (closed_first,closed_second)):
        raise ValueError('COMPOSITE_SESSION_DAY_OR_IDLE_WINDOW_RED')
    return {'schema':'rc6.a161.composed-runtime-acceptance.v1','status':'GREEN',
            'candidate_sha':candidate,'candidate_tree':tree,'deploy_sha':deploy,
            'open_provisional':opened,'closed_provisional':closed,
            'preopen_admin_cause_closed':True,'eod_ready_durable':True,
            'release_and_containers_unchanged':True,'real_orders_sent':0,
            'operational_go':False}


if __name__=='__main__':
    try:
        if len(sys.argv)==7 and sys.argv[1] in {'OPEN','CLOSED'}:
            phase=sys.argv[1]
            result=compare(read(sys.argv[2]),read(sys.argv[3]),
                           *sys.argv[4:],phase=phase)
            prefix='RC6_A161_TWO_PULSE='
        elif len(sys.argv)==11 and sys.argv[1]=='COMPOSE':
            result=compose(*(read(path) for path in sys.argv[2:6]),
                           *(read_admin(path) for path in sys.argv[6:8]),
                           *sys.argv[8:])
            prefix='RC6_A161_COMPOSED='
        else:
            raise ValueError('EXPECTED_PHASE_OR_COMPOSITE_RECEIPTS')
    except Exception as exc:
        code=str(exc).split(':',1)[0]
        print('RC6_A161_TWO_PULSE=RED|'+(
            code if code.isascii() and code.isupper() and len(code)<80
            else type(exc).__name__.upper()))
        raise SystemExit(2)
    print(prefix+json.dumps(result,sort_keys=True,
          separators=(',',':')))
