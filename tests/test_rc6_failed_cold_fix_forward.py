"""Preserve the original failed input, never rehabilitate its old cut."""
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import pytest
from rc6_shadow_runtime import cold_audit as c, persistence as p
from scripts import rc6_failed_cold_fix_forward as recovery
from scripts import rc6_cold2_successor
from tests.rc6_native_storage import native_storage
from tests.test_rc6_shadow_cold_audit import seal, publish_reference


def test_absolute_entrypoint_imports_successor_from_host_root(tmp_path):
    script=Path(recovery.__file__).resolve()
    root=script.parents[1]
    probe='''import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('rc6_entrypoint',sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ROOT=Path(sys.argv[2])
archive=module.ROOT/'data/deploy/rc6-failed-cold-89e7f81dbc9b4a628bdc30edf86a17b0'
assert module.proven_distinct_failed_successor(
    archive,'a'*64,module.CURRENT_FAILED_SUCCESSOR) is False
print('COLD_ENTRYPOINT_IMPORT_PATH=GREEN')
'''
    result=subprocess.run([sys.executable,'-I','-c',probe,str(script),str(root)],
                          cwd=tmp_path,text=True,capture_output=True,timeout=20)
    assert result.returncode==0, result.stderr
    assert result.stdout.strip()=='COLD_ENTRYPOINT_IMPORT_PATH=GREEN'


def test_distinct_failed_successor_requires_exact_bound_current(monkeypatch, tmp_path):
    archive=tmp_path/'data/deploy/rc6-failed-cold-original'
    evidence={'snapshot_id':recovery.CURRENT_FAILED_SUCCESSOR,
              'manifest_sha256':recovery.CURRENT_FAILED_MANIFEST,
              'pack_bytes':219680584,
              'failed_computed_report':{'sha256':recovery.CURRENT_FAILED_REPORT_SHA},
              'cut':'a'*64,'queue_failed':True}
    monkeypatch.setattr(rc6_cold2_successor,'inspect',lambda root:evidence)
    assert recovery.proven_distinct_failed_successor(archive,'a'*64,
                                                      recovery.CURRENT_FAILED_SUCCESSOR)
    assert not recovery.proven_distinct_failed_successor(archive,'b'*64,
                                                          recovery.CURRENT_FAILED_SUCCESSOR)
    assert not recovery.proven_distinct_failed_successor(archive,'a'*64,'other')
    for key,value in [('manifest_sha256','0'*64),('queue_failed',False),
                      ('failed_computed_report',{'sha256':'0'*64})]:
        changed={**evidence,key:value}
        monkeypatch.setattr(rc6_cold2_successor,'inspect',lambda root, v=changed:v)
        assert not recovery.proven_distinct_failed_successor(archive,'a'*64,
                                                              recovery.CURRENT_FAILED_SUCCESSOR)


def test_closed_flat_clean_stop_cold_frame_cancellation_from_host_receipt():
    # The Oct 22 host had no carryover. _frame() cancelled 1.9 seconds before
    # Docker's clean exit; the archived failed cut must remain revoked.
    q = {'paused_postclose': False, 'intraday_caucion': False,
         'market_open_suspended': False, 'open_paper_carryover': False,
         'spot_carryover': None, 'caucion_carryover': None,
         'stop_evidence': {'porota_production_observer': {
             'classification': 'CLEAN_EXIT',
             'before': {'Status': 'running',
                        'StartedAt': '2026-10-22T00:02:50.313220733Z'},
             'after': {'Status': 'exited', 'ExitCode': 0,
                       'OOMKilled': False,
                       'FinishedAt': '2026-10-22T00:40:58.147470735Z'}}}}
    live = {'status': 'FAIL_CLOSED', 'error_class': 'ValueError',
            'error_module': 'cold_audit', 'error_line': 265,
            'as_of': '2026-10-22T00:40:56.246642+00:00',
            'real_orders_sent': 0, 'real_routes': 'NOT_CALLED'}
    assert recovery.proven_stop_cancellation(live, q)
    assert recovery.proven_stop_cancellation({**live, 'error_line': 360}, q)
    assert not recovery.proven_stop_cancellation({**live, 'error_line': 264}, q)
    assert not recovery.proven_stop_cancellation({**live, 'error_module': 'other'}, q)
    assert not recovery.proven_stop_cancellation({**live,
        'as_of': '2026-10-22T00:40:40+00:00'}, q)
    assert not recovery.proven_stop_cancellation(live, {**q,
        'caucion_carryover': {'open_count': 1}})
    assert recovery.proven_stop_cancellation(live, {**q,
        'market_open_suspended': True})
    assert not recovery.proven_stop_cancellation(live, {**q,
        'market_open_suspended': True, 'open_paper_carryover': True})
    assert not recovery.proven_stop_cancellation(live, {**q,
        'stop_evidence': {'porota_production_observer': {
            **q['stop_evidence']['porota_production_observer'],
            'classification': 'UNKNOWN'}}})


def test_oct23_closed_flat_stage_cancellation_requires_exact_clean_stop(tmp_path):
    stop={'classification':'CLEAN_EXIT',
          'before':{'Status':'running','StartedAt':'2026-10-22T23:49:15.51107753Z'},
          'after':{'Status':'exited','ExitCode':0,'OOMKilled':False,
                   'FinishedAt':recovery.CLOSED_FLAT_STAGE_STOP_FINISHED}}
    flat={'paused_postclose':False,'intraday_caucion':False,
          'pending_caucion':False,'market_open_suspended':False,
          'open_paper_carryover':False,'spot_carryover':None,
          'caucion_carryover':None}
    containers={name:{'id':name+'-id','image':'sha256:'+'a'*64}
                for name in ('porota_production_observer','porota_production_dashboard')}
    prior={**flat,'candidate_sha':recovery.CLOSED_FLAT_STAGE_STOP_SHA,
           'status':'FIX_FORWARD_VALIDATION_REQUIRED','installation_started':True,
           'inflight':'ABSENT_OWNED_RUNTIME_STOPPED','real_orders_sent':0,
           'ppi_watch_accessed':False,'containers':containers,
           'stop_evidence':{'porota_production_observer':stop,
                            'porota_production_dashboard':{'classification':'CLEAN_EXIT'}}}
    live={'status':'FAIL_CLOSED','error_class':'ValueError',
          'error_module':'cold_audit','error_line':360,
          'as_of':'2026-10-23T22:18:21.695017+00:00',
          'real_orders_sent':0,'real_routes':'NOT_CALLED'}
    assert recovery.proven_stop_cancellation(live,prior)
    assert recovery.proven_stop_cancellation(live,{**prior,'candidate_sha':'f'*40})
    assert not recovery.proven_stop_cancellation({**live,'error_line':361},prior)
    assert not recovery.proven_stop_cancellation(
        {**live,'as_of':'2026-10-23T22:18:01+00:00'},prior)
    path=tmp_path/('quiescence-'+recovery.CLOSED_FLAT_STAGE_STOP_SHA+'.json')
    path.write_text(json.dumps(prior));path.chmod(0o600)
    current={**prior,'candidate_sha':'e'*40,'status':'HOST_INSTALLATION_STARTED',
             'fix_forward_from':recovery.CLOSED_FLAT_STAGE_STOP_SHA,
             'prior_receipt_sha256':recovery.hashlib.sha256(path.read_bytes()).hexdigest()}
    assert recovery.proven_inherited_closed_flat_stop_cancellation(live,current,path)
    assert not recovery.proven_inherited_closed_flat_stop_cancellation(live,
        {**current,'prior_receipt_sha256':'0'*64},path)
    assert not recovery.proven_inherited_closed_flat_stop_cancellation(live,
        {**current,'containers':{**containers,'porota_production_observer':{'id':'changed'}}},path)
    assert not recovery.proven_inherited_closed_flat_stop_cancellation(live,
        {**current,'pending_caucion':True},path)


def test_oct25_closed_flat_stage_cancellation_and_next_candidate(tmp_path):
    # Read-only host run 40154425603: status.json line 360 was written 1.334s
    # before both owned containers completed a clean, flat CLOSED stop.
    prior_sha='26a95f734cb9eece72e89a1d084c7006a1e05fa2'
    stop={'classification':'CLEAN_EXIT',
          'before':{'Status':'running','StartedAt':'2026-10-23T22:59:18.778076998Z'},
          'after':{'Status':'exited','ExitCode':0,'OOMKilled':False,
                   'FinishedAt':'2026-10-25T02:20:54.688529492Z'}}
    evidence={'porota_production_observer':stop,
              'porota_production_dashboard':{'classification':'CLEAN_EXIT'}}
    containers={name:{'id':name+'-id','image':'sha256:'+'a'*64}
                for name in evidence}
    flat={'paused_postclose':False,'intraday_caucion':False,
          'pending_caucion':False,'market_open_suspended':False,
          'open_paper_carryover':False,'spot_carryover':None,
          'caucion_carryover':None}
    prior={**flat,'candidate_sha':prior_sha,
           'status':'FIX_FORWARD_VALIDATION_REQUIRED','installation_started':True,
           'inflight':'ABSENT_OWNED_RUNTIME_STOPPED','real_orders_sent':0,
           'ppi_watch_accessed':False,'stop_evidence':evidence,
           'containers':containers}
    live={'status':'FAIL_CLOSED','error_class':'ValueError',
          'error_module':'cold_audit','error_line':360,
          'as_of':'2026-10-25T02:20:53.354404+00:00',
          'real_orders_sent':0,'real_routes':'NOT_CALLED'}
    assert recovery.proven_stop_cancellation(live,prior)
    path=tmp_path/('quiescence-'+prior_sha+'.json')
    raw=json.dumps(prior,sort_keys=True).encode()
    path.write_bytes(raw);path.chmod(0o600)
    current={**prior,'candidate_sha':'e'*40,
             'status':'HOST_INSTALLATION_STARTED','fix_forward_from':prior_sha,
             'prior_receipt_sha256':recovery.hashlib.sha256(raw).hexdigest()}
    assert recovery.proven_inherited_closed_flat_stop_cancellation(live,current,path)
    for changed in ({'prior_receipt_sha256':'0'*64},
                    {'fix_forward_from':'f'*40},
                    {'market_open_suspended':True},
                    {'stop_evidence':{'porota_production_observer':{
                        **stop,'classification':'UNKNOWN'}}},
                    {'containers':{**containers,'porota_production_observer':{'id':'wrong'}}}):
        assert not recovery.proven_inherited_closed_flat_stop_cancellation(
            live,{**current,**changed},path)
    for changed in ({'error_line':361},{'as_of':'2026-10-25T02:20:20Z'},
                    {'real_routes':'CALLED'}):
        assert not recovery.proven_stop_cancellation({**live,**changed},prior)
    path.write_bytes(raw+b' ')
    assert not recovery.proven_inherited_closed_flat_stop_cancellation(live,current,path)


def test_open_flat_clean_stop_inherited_by_exact_failed_deploy(tmp_path):
    # #703 stopped a flat OPEN PAPER observer while _frame flushed its segment.
    # The live FAIL_CLOSED status precedes the clean exit by 5.19 seconds.
    prior_sha='83855c3b08647243dcd706ca7ead929378c4c11d'
    stop={'classification':'CLEAN_EXIT',
        'before':{'Status':'running','StartedAt':'2026-10-22T13:58:50.536918847Z'},
        'after':{'Status':'exited','ExitCode':0,'OOMKilled':False,
                 'FinishedAt':'2026-10-22T15:22:02.310428782Z'}}
    evidence={'porota_production_observer':stop,
              'porota_production_dashboard':{'classification':'CLEAN_EXIT'}}
    containers={name:{'id':name+'-id','image':'sha256:'+'a'*64}
                for name in evidence}
    flat={'paused_postclose':False,'intraday_caucion':False,
          'market_open_suspended':True,'open_paper_carryover':False,
          'spot_carryover':None,'caucion_carryover':None}
    prior={**flat,'candidate_sha':prior_sha,
           'status':'FIX_FORWARD_VALIDATION_REQUIRED',
           'installation_started':True,'inflight':'ABSENT_OWNED_RUNTIME_STOPPED',
           'real_orders_sent':0,'ppi_watch_accessed':False,
           'stop_evidence':evidence,'containers':containers}
    path=tmp_path/('quiescence-'+prior_sha+'.json')
    raw=json.dumps(prior,sort_keys=True).encode()
    path.write_bytes(raw);path.chmod(0o600)
    current={**flat,'candidate_sha':'f'*40,'fix_forward_from':prior_sha,
             'prior_receipt_sha256':recovery.hashlib.sha256(raw).hexdigest(),
             'status':'HOST_INSTALLATION_STARTED','real_orders_sent':0,
             'ppi_watch_accessed':False,'stop_evidence':evidence,
             'containers':containers}
    live={'status':'FAIL_CLOSED','error_class':'ValueError',
          'error_module':'cold_audit','error_line':265,
          'as_of':'2026-10-22T15:21:57.117973+00:00',
          'real_orders_sent':0,'real_routes':'NOT_CALLED'}
    assert recovery.proven_stop_cancellation(live,prior)
    assert recovery.proven_inherited_flat_stop_cancellation(live,current,path)
    for changed in ({'prior_receipt_sha256':'0'*64},
                    {'fix_forward_from':'e'*40},
                    {'market_open_suspended':False},
                    {'stop_evidence':{**evidence,'porota_production_observer':
                                      {**stop,'classification':'UNKNOWN'}}},
                    {'containers':{**containers,'porota_production_observer':
                                   {**containers['porota_production_observer'], 'id':'wrong'}}}):
        assert not recovery.proven_inherited_flat_stop_cancellation(
            live,{**current,**changed},path)
    for changed in ({'error_line':361}, {'real_orders_sent':1},
                    {'as_of':'2026-10-22T15:21:30+00:00'}):
        assert not recovery.proven_inherited_flat_stop_cancellation(
            {**live,**changed},current,path)
    path.write_bytes(raw+b' ')
    assert not recovery.proven_inherited_flat_stop_cancellation(live,current,path)


def test_pending_paper_caucion_cold_cancellation_and_hashed_fix_forward(tmp_path):
    # Read-only Actions #39866095743: cold_audit:360 at 22:32:42.761654Z,
    # clean Docker exit at 22:32:47.063944893Z, one reconciled PAPER caucion.
    stop={'classification':'CLEAN_EXIT',
          'before':{'Status':'running','StartedAt':'2026-10-22T21:00:00Z'},
          'after':{'Status':'exited','ExitCode':0,'OOMKilled':False,
                   'FinishedAt':'2026-10-22T22:32:47.063944893Z'}}
    carry={'open_count':1,'maturity_at':'2026-10-23T17:00:00-03:00',
           'allocation_reconciled':True,'sha256':'a'*64,
           'audit_source_sha256':
               'b1d711899a5c85f4efc72e2632a9d81cdddf0a6dc71dee82d5c42923b5639865'}
    mode={'pending_caucion':True,'paused_postclose':False,
          'intraday_caucion':False,'market_open_suspended':False,
          'open_paper_carryover':False,'spot_carryover':None,
          'caucion_carryover':carry}
    evidence={'porota_production_observer':stop,
              'porota_production_dashboard':{'classification':'CLEAN_EXIT'}}
    containers={name:{'id':name+'-id','image':'sha256:'+'a'*64}
                for name in evidence}
    candidate='d1f9691404c4a9145e91a95ca1f2bbf9b137d138'
    prior={**mode,'candidate_sha':candidate,'status':'FIX_FORWARD_VALIDATION_REQUIRED',
           'installation_started':True,'inflight':'ABSENT_OWNED_RUNTIME_STOPPED',
           'real_orders_sent':0,'ppi_watch_accessed':False,
           'stop_evidence':evidence,'containers':containers}
    live={'status':'FAIL_CLOSED','error_class':'ValueError',
          'error_module':'cold_audit','error_line':360,
          'as_of':'2026-10-22T22:32:42.761654+00:00',
          'real_orders_sent':0,'real_routes':'NOT_CALLED'}
    assert recovery.proven_stop_cancellation(live,prior)
    path=tmp_path/('quiescence-'+candidate+'.json')
    raw=json.dumps(prior,sort_keys=True).encode()
    path.write_bytes(raw);path.chmod(0o600)
    current={**prior,'candidate_sha':'f'*40,'fix_forward_from':candidate,
             'status':'HOST_INSTALLATION_STARTED',
             'prior_receipt_sha256':recovery.hashlib.sha256(raw).hexdigest()}
    assert recovery.proven_inherited_pending_stop_cancellation(live,current,path)
    for changed in ({'pending_caucion':False},{'caucion_carryover':{**carry,'sha256':'0'*64}},
                    {'prior_receipt_sha256':'0'*64},
                    {'stop_evidence':{**evidence,'porota_production_observer':
                                      {**stop,'classification':'UNKNOWN'}}}):
        assert not recovery.proven_inherited_pending_stop_cancellation(
            live,{**current,**changed},path)
    for changed in ({'error_line':361},{'as_of':'2026-10-22T22:32:20Z'}):
        assert not recovery.proven_stop_cancellation({**live,**changed},prior)
    assert not recovery.proven_stop_cancellation(live,{**prior,
        'caucion_carryover':{**carry,'audit_source_sha256':'0'*64}})
    path.write_bytes(raw+b' ')
    assert not recovery.proven_inherited_pending_stop_cancellation(live,current,path)


def test_unchanged_previous_cancellation_requires_hashed_receipt_and_new_clean_stop(
        tmp_path, monkeypatch):
    old_stop = {'classification': 'CLEAN_EXIT',
        'before': {'Status': 'running', 'StartedAt': '2026-10-22T00:02:50Z'},
        'after': {'Status': 'exited', 'ExitCode': 0, 'OOMKilled': False,
                  'FinishedAt': '2026-10-22T00:40:58Z'}}
    previous = {'candidate_sha': recovery.PRIOR_CANCELLED_SHA,
        'status': 'FIX_FORWARD_VALIDATION_REQUIRED', 'installation_started': True,
        'real_orders_sent': 0, 'ppi_watch_accessed': False,
        'paused_postclose': False, 'intraday_caucion': False,
        'market_open_suspended': False, 'open_paper_carryover': False,
        'spot_carryover': None, 'caucion_carryover': None,
        'stop_evidence': {'porota_production_observer': old_stop}}
    path = tmp_path/'quiescence-prior.json'
    raw = json.dumps(previous, sort_keys=True).encode()
    path.write_bytes(raw);path.chmod(0o600)
    monkeypatch.setattr(recovery, 'PRIOR_CANCELLED_RECEIPT_SHA',
                        recovery.hashlib.sha256(raw).hexdigest())
    live = {'status': 'FAIL_CLOSED', 'error_class': 'ValueError',
        'error_module': 'cold_audit', 'error_line': 265,
        'as_of': '2026-10-22T00:40:56.246642+00:00',
        'real_orders_sent': 0, 'real_routes': 'NOT_CALLED'}
    new_stop = {'classification': 'CLEAN_EXIT',
        'before': {'Status': 'running', 'StartedAt': '2026-10-22T00:42:18Z'},
        'after': {'Status': 'exited', 'ExitCode': 0, 'OOMKilled': False,
                  'FinishedAt': '2026-10-22T01:15:00Z'}}
    current = {'candidate_sha': 'f'*40, 'status': 'HOST_INSTALLATION_STARTED',
        'real_orders_sent': 0, 'ppi_watch_accessed': False,
        'paused_postclose': False, 'intraday_caucion': False,
        'market_open_suspended': False, 'open_paper_carryover': False,
        'spot_carryover': None, 'caucion_carryover': None,
        'stop_evidence': {name: new_stop for name in
            ('porota_production_observer','porota_production_dashboard')}}
    assert recovery.proven_prior_stop_cancellation(live, current, path)
    assert not recovery.proven_prior_stop_cancellation({**live,
        'real_orders_sent': 1}, current, path)
    assert not recovery.proven_prior_stop_cancellation(live, {**current,
        'market_open_suspended': True}, path)
    assert not recovery.proven_prior_stop_cancellation(live, {**current,
        'stop_evidence': {'porota_production_observer': new_stop}}, path)
    assert not recovery.proven_prior_stop_cancellation(live, {**current,
        'stop_evidence': {**current['stop_evidence'],
            'porota_production_dashboard': {**new_stop, 'classification': 'UNKNOWN'}}}, path)
    path.write_bytes(raw+b' ')
    assert not recovery.proven_prior_stop_cancellation(live, current, path)


def test_exact_failed_backlog_archive_then_fresh_complete_generation(tmp_path,monkeypatch):
    native=tmp_path/'native';native.mkdir()
    monkeypatch.setattr(p,'_native_json_bound',native_storage(native))
    root=tmp_path/'porota';shadow=root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    deploy=root/'data/deploy';deploy.mkdir(parents=True)
    with p.EvidenceFiles(shadow) as files:
        old=seal(files,{'values':['actual',1]})
        publish_reference(files,old)
        old_cut=files.read('CURRENT.json')['generation_id']
        with monkeypatch.context() as change:
            change.setattr(c.time,'time',lambda:old['deadline_at']+.001)
            with pytest.raises(ValueError,match='DEADLINE'):c.drain(files)
        files.write('status.json',{'status':'FAIL_CLOSED','real_orders_sent':0})
        with c.Directory(files) as directory:manifest=directory.read('manifest-'+old['snapshot_id']+'.json')
    monkeypatch.setattr(recovery,'ROOT',root)
    monkeypatch.setattr(recovery,'IDENT',old['snapshot_id'])
    monkeypatch.setattr(recovery,'MANIFEST_SHA',p.digest(manifest))
    monkeypatch.setattr(recovery,'CUT',old_cut)
    monkeypatch.setattr(recovery,'FILES',{old['snapshot_id']+'.pack',
        'manifest-'+old['snapshot_id']+'.json','receipt-'+old['snapshot_id']+'.json','queue.json'})
    new_sha=recovery.PRIOR_ARCHIVE_SHA
    containers={name:{'id':name+'-id','image':'sha256:'+('a'*64)} for name in
                ('porota_production_observer','porota_production_dashboard')}
    (deploy/('quiescence-'+new_sha+'.json')).write_text(json.dumps({
        'status':'HOST_INSTALLATION_STARTED','candidate_sha':new_sha,'real_orders_sent':0,
        'stop_evidence':{name:{} for name in containers},'containers':containers}))
    monkeypatch.setattr(recovery.subprocess,'check_output',lambda args,**kw:
        containers[args[-1]]['id']+'|running|'+containers[args[-1]]['image']+'\n')
    with pytest.raises(ValueError,match='NOT_STOPPED'):
        recovery.run(new_sha)
    assert (shadow/'cold-audit').is_dir()
    monkeypatch.setattr(recovery.subprocess,'check_output',lambda args,**kw:
        containers[args[-1]]['id']+'|exited|'+containers[args[-1]]['image']+'\n')
    recovery.run(new_sha)
    recovery.run(new_sha)  # Crash after the atomic rename is idempotent.
    archive=deploy/('rc6-failed-cold-'+old['snapshot_id'])
    assert archive.is_dir() and not (shadow/'cold-audit').exists()
    assert json.loads((deploy/('rc6-cold-fix-forward-'+new_sha+'.json')).read_text())['state']=='ARCHIVED_OLD_CUT_STILL_REVOKED'
    # A later snapshot can legitimately recreate the active namespace while
    # the failed ID remains archived. This was the real Droplet state in #624.
    active=shadow/'cold-audit';active.mkdir(mode=0o700)
    foreign='a'*32
    (active/('manifest-'+foreign+'.json')).write_text('foreign record retained')
    queue={'snapshot_id':foreign,'state':'FAILED'}
    queue_path=active/'queue.json'
    queue_path.write_text(json.dumps({'payload':queue,'sha256':p.digest(queue)}))
    queue_path.chmod(0o600)
    recovery.run(new_sha)
    assert (active/('manifest-'+foreign+'.json')).read_text()=='foreign record retained'
    assert archive.is_dir()
    (active/('manifest-'+old['snapshot_id']+'.json')).write_text('conflict')
    with pytest.raises(ValueError,match='COLD_AMBIGUOUS_ARCHIVE'):
        recovery.run(new_sha)
    (active/('manifest-'+old['snapshot_id']+'.json')).unlink()
    conflicting_queue={'snapshot_id':old['snapshot_id'],'state':'FAILED'}
    queue_path.write_text(json.dumps({'payload':conflicting_queue,
        'sha256':p.digest(conflicting_queue)}))
    with pytest.raises(ValueError,match='COLD_AMBIGUOUS_ARCHIVE'):
        recovery.run(new_sha)
    queue_path.write_text(json.dumps({'payload':queue,'sha256':p.digest(queue)}))
    with p.EvidenceFiles(shadow) as files:
        report={'as_of':'2026-10-05T13:20:10+00:00','status':'SHADOW_OBSERVING',
            'real_orders_sent':0,'real_routes':'NOT_CALLED',
            'computed_report':{'snapshot_id':foreign,'state':'PENDING'}}
        status={'as_of':report['as_of'],'status':'SHADOW_OBSERVING',
            'configuration_fingerprint':'fixed','real_orders_sent':0,
            'real_routes':'NOT_CALLED','report_digest':p.digest(report)}
        files.publish_generation(report,status)
        files.write('status.json',{'status':'FAIL_CLOSED','real_orders_sent':0})
    (shadow/('cut-'+old_cut+'.json.gz')).unlink(missing_ok=True)
    recovery.run(new_sha)  # New pointer and bounded GC must not resurrect old ID.
    assert json.loads((deploy/('rc6-cold-fix-forward-'+new_sha+'.json')).read_text())['old_cut_gc_complete']
    successor='f'*40
    (deploy/('quiescence-'+successor+'.json')).write_text(json.dumps({
        'status':'HOST_INSTALLATION_STARTED','candidate_sha':successor,'real_orders_sent':0,
        'stop_evidence':{name:{} for name in containers},'containers':containers}))
    with p.EvidenceFiles(shadow) as files:
        files.write('status.json',{'status':'SHADOW_OBSERVING',
            'real_orders_sent':0,'real_routes':'NOT_CALLED'})
    prior_path=deploy/('rc6-cold-fix-forward-'+new_sha+'.json')
    prior_bytes=prior_path.read_bytes()
    recovery.run(successor)
    adopted=json.loads((deploy/('rc6-cold-fix-forward-'+successor+'.json')).read_text())
    assert adopted['archived_recovery'] is True
    assert adopted['adopted_prior_receipt_sha256']==recovery.hashlib.sha256(prior_bytes).hexdigest()
    assert adopted['current_cut']!=old_cut and adopted['old_cut_gc_complete'] is True
    assert (active/('manifest-'+foreign+'.json')).read_text()=='foreign record retained'
    prior=json.loads(prior_bytes);prior['state']='UNVERIFIED'
    prior_path.write_text(json.dumps(prior))
    with pytest.raises(ValueError,match='COLD_PRIOR_ARCHIVE_RECEIPT_CHANGED'):
        recovery.run(successor)
    prior_path.write_bytes(prior_bytes)
    with p.EvidenceFiles(shadow) as files:
        files.write('status.json',{'status':'FAIL_CLOSED','real_orders_sent':0,
                                   'real_routes':'NOT_CALLED'})
    with monkeypatch.context() as exact:
        exact.setattr(recovery,'proven_distinct_failed_successor',lambda *_:True)
        recovery.run(successor)  # A separately proved FAILED cut can wait for its own guard.
    with pytest.raises(ValueError,match='COLD_ARCHIVED_RECOVERY_NOT_PROVEN'):
        recovery.run(successor)
    stopped = datetime.now(timezone.utc)
    qpath = deploy/('quiescence-'+successor+'.json')
    q = json.loads(qpath.read_text())
    q['paused_postclose'] = True
    q['stop_evidence']['porota_production_observer'] = {
        'classification': 'CLEAN_EXIT',
        'before': {'Status': 'running', 'StartedAt':
                   (stopped-timedelta(minutes=2)).isoformat()},
        'after': {'Status': 'exited', 'ExitCode': 0, 'OOMKilled': False,
                  'FinishedAt': stopped.isoformat()}}
    qpath.write_text(json.dumps(q))
    cancellation = {'status': 'FAIL_CLOSED', 'error_class': 'ValueError',
        'error_module': 'cold_audit', 'error_line': 360,
        'as_of': (stopped-timedelta(seconds=1)).isoformat(),
        'real_orders_sent': 0, 'real_routes': 'NOT_CALLED'}
    with p.EvidenceFiles(shadow) as files:files.write('status.json',cancellation)
    recovery.run(successor)  # Exact SIGTERM cancellation after a proven clean stop.
    with p.EvidenceFiles(shadow) as files:
        files.write('status.json',{**cancellation,'error_line': 361})
    with pytest.raises(ValueError,match='COLD_ARCHIVED_RECOVERY_NOT_PROVEN'):
        recovery.run(successor)
    with p.EvidenceFiles(shadow) as files:
        files.write('status.json',{**cancellation,
            'as_of': (stopped-timedelta(seconds=60)).isoformat()})
    with pytest.raises(ValueError,match='COLD_ARCHIVED_RECOVERY_NOT_PROVEN'):
        recovery.run(successor)
    q['stop_evidence']['porota_production_observer']['classification'] = 'UNKNOWN'
    qpath.write_text(json.dumps(q))
    with p.EvidenceFiles(shadow) as files:files.write('status.json',cancellation)
    with pytest.raises(ValueError,match='COLD_ARCHIVED_RECOVERY_NOT_PROVEN'):
        recovery.run(successor)
    q['stop_evidence']['porota_production_observer']['classification'] = 'CLEAN_EXIT'
    q.update(paused_postclose=False,market_open_suspended=True,
             open_paper_carryover=False,intraday_caucion=True,
             spot_carryover=None,caucion_carryover={
                 'open_count':1,'sha256':'d'*64,
                 'maturity_at':'2026-10-21T17:00:00-03:00'})
    qpath.write_text(json.dumps(q))
    recovery.run(successor)  # The exact intraday caucion survives the clean stop.
    q['caucion_carryover']['sha256']='invalid'
    qpath.write_text(json.dumps(q))
    with pytest.raises(ValueError,match='COLD_ARCHIVED_RECOVERY_NOT_PROVEN'):
        recovery.run(successor)
    q['caucion_carryover']['sha256']='d'*64
    q['open_paper_carryover']=True
    qpath.write_text(json.dumps(q))
    with pytest.raises(ValueError,match='COLD_ARCHIVED_RECOVERY_NOT_PROVEN'):
        recovery.run(successor)
    with p.EvidenceFiles(shadow) as files:
        files.write('status.json',{'status':'SHADOW_OBSERVING',
            'real_orders_sent':0,'real_routes':'NOT_CALLED'})
    (active/('manifest-'+foreign+'.json')).unlink()
    queue_path.unlink();active.rmdir()
    with pytest.raises(ValueError):p.read_committed_generation(shadow)
    with p.EvidenceFiles(shadow) as files:
        with c.Snapshot(files) as snapshot:
            fresh=snapshot.seal({'values':['new',2]},source_as_of='2026-10-05T13:20:30+00:00',configuration='fixed')
        assert c.drain(files)['state']=='COMPLETE'
        publish_reference(files,fresh)
    assert p.read_committed_generation(shadow)['computed_audit']['state']=='COMPLETE'
    assert (archive/('receipt-'+old['snapshot_id']+'.json')).exists()
