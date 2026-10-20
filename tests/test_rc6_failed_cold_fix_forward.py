"""Preserve the original failed input, never rehabilitate its old cut."""
import json
import pytest
from rc6_shadow_runtime import cold_audit as c, persistence as p
from scripts import rc6_failed_cold_fix_forward as recovery
from tests.rc6_native_storage import native_storage
from tests.test_rc6_shadow_cold_audit import seal, publish_reference


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
        files.write('status.json',{'status':'FAIL_CLOSED','real_orders_sent':0})
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
