"""Actual Python import/exec observations; no BIG or business-runtime replay.

Observer unit controls use physical source identities, not a fabricated raw-Git
qualification. The future governed runner supplies wholeSource/157 authority.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import hashlib, importlib, importlib.util, json, os, pathlib, stat, sys, types
root = pathlib.Path(sys.argv[1]); private = pathlib.Path(sys.argv[2]); case = sys.argv[3]
sys.path.insert(0,str(root)); sys.dont_write_bytecode=True
from scripts import rc6_native_import_provenance as proof
files={}
for path in root.rglob('*.py'):
    if path.is_file() and not path.is_symlink():
        wire=path.read_bytes(); name=path.relative_to(root).as_posix()
        files[name]={'sha256':hashlib.sha256(wire).hexdigest(),
          'mode':'100'+format(stat.S_IMODE(path.stat().st_mode),'03o'),
          'blob_id':hashlib.sha1(b'blob '+str(len(wire)).encode()+b'\0'+wire).hexdigest()}
binding={'source_root':str(root),'source_sha':'a'*40,'source_tree':'b'*40,
  'source_index_sha256':'c'*64,'files':files,'prepared_by_pid':os.getpid(),
  'test_binding_scope':'PHYSICAL_OBSERVER_UNIT_CONTROL_NOT_RAW_GIT_QUALIFICATION'}
observer=proof.NativeImportObserver(binding,role='GUARD_CHILD')
initial=observer.initial_receipt()
if case=='retained':
    importlib.import_module('scripts.rc6_sqlite_scratch_guard')
elif case=='alien_deleted':
    module_path=private/'alien_import.py'; module_path.write_text('VALUE=17\n')
    spec=importlib.util.spec_from_file_location('rc6_deleted_alien_import',module_path)
    module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module
    spec.loader.exec_module(module); del sys.modules[spec.name]
elif case=='opaque':
    exec(compile('VALUE=11','<string>','exec'),{})
elif case=='none_unresolved':
    sys.audit('import','rc6_missing_observed_module',None,None,None,None)
elif case=='overflow':
    # Public audit events with missing origins are a typed telemetry control,
    # not a claim that these fabricated names were imported.
    for index in range(proof.MAX_RECORDS+1):
        sys.audit('import','rc6_counter_control_'+str(index),None,None,None,None)
elif case=='deactivation':
    observer.deactivate(); before=dict(observer.counts)
    exec(compile('VALUE=1','<string>','exec'),{})
    assert observer.counts==before
    next_observer=proof.NativeImportObserver(binding,role='NEXT_GUARD_CHILD')
    exec(compile('VALUE=2','<string>','exec'),{})
    next_result=next_observer.finish()
    assert next_result['event_counts']['opaque_exec']>=1 and observer.counts==before
elif case=='snapshot_failure':
    observer.snapshot=lambda: (_ for _ in ()).throw(RuntimeError('snapshot fault'))
    try: observer.finish()
    except RuntimeError: pass
    else: raise AssertionError('snapshot fault lost')
    assert observer.active is False
    observer.snapshot=lambda: initial['boundary_before']
elif case in ('child_initial_abort','child_queue_abort'):
    from scripts import rc6_issue465_stress as stress
    created=[]
    original=proof.NativeImportObserver
    proof.environment_qualification=lambda *a: {'test_scope':'CONTROLLED_CHILD_STARTUP_NOT_ACTUAL157'}
    def owned(*args,**kwargs):
        owned_observer=original(*args,**kwargs); created.append(owned_observer)
        if case=='child_initial_abort':
            owned_observer.initial_receipt=lambda: (_ for _ in ()).throw(SystemExit('initial abort'))
        return owned_observer
    proof.NativeImportObserver=owned
    class Signal:
        def set(self): pass
    class Queue:
        def put(self,message):
            if case=='child_queue_abort': raise SystemExit('queue abort')
            raise AssertionError('unexpected startup message')
    try:
        stress._shadow_child(str(private/'absent.db'),str(private/'absent-output'),
            Signal(),Signal(),Queue(),False,128*1024**2,True,None,
            dict(binding,prepared_by_pid=os.getppid()))
    except SystemExit: pass
    else: raise AssertionError('startup BaseException not propagated')
    assert len(created)==1 and created[0].active is False
    prior=dict(created[0].counts)
    exec(compile('VALUE=5','<string>','exec'),{})
    assert created[0].counts==prior
    next_observer=original(binding,role='AFTER_CHILD_STARTUP_ABORT')
    exec(compile('VALUE=6','<string>','exec'),{})
    assert next_observer.finish()['event_counts']['opaque_exec']>=1
    assert not (private/'absent.db').exists() and not (private/'absent-output').exists()
elif case=='nonzero_child':
    import multiprocessing, sqlite3, time
    from scripts import rc6_issue465_stress as stress
    fork=multiprocessing.get_context('fork')
    stress.mp.get_context=lambda mode: fork
    proof.prepare_binding=lambda **kwargs: dict(binding,
        parent_environment_before_fixtures={'test_scope':'CONTROLLED_BINDING_NOT_RAW_GIT_QUALIFICATION'},
        sys_path_before_fixtures=list(sys.path))
    def fixture(path,**kwargs):
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE observer_guard(value INTEGER)')
            connection.execute('INSERT INTO observer_guard VALUES(1)')
    def failure(*args,**kwargs): raise ImportError('controlled work startup failure')
    stress.fixture_database=fixture; stress._shadow_child_work=failure
    stress.factual_exit_probe=lambda path: {'started_at_monotonic':time.monotonic(),
        'finished_at_monotonic':time.monotonic(),'closed':0,'sell_fills':0,
        'scope':'CONTROLLED_EXIT_STUB_NO_TRADING_OR_FINANCIAL_ACCEPTANCE'}
    try:
        stress.run_stress(private/'nonzero-child',catalog_count=1,observations_per_identity=1,
            canonical_runtime=True,source_provenance={'test_scope':'CONTROLLED_BINDING'})
    except stress.StressResourceLimit as error: nonzero_evidence=error.evidence
    else: raise AssertionError('nonzero child lost typed evidence or became GREEN')
    assert nonzero_evidence['business_resource_complete'] is False
    assert nonzero_evidence['import_proof_complete'] is False
    aggregate=nonzero_evidence['import_provenance']
    assert aggregate['worker_exitcode']==1
    assert aggregate['worker_boundary_before']['native_pid']==aggregate['worker_pid']!=os.getpid()
    assert aggregate['worker_boundary_before']['parent_pid']==os.getpid()
    assert aggregate['worker_final']['status']=='UNVERIFIED_WORKER_EXCEPTION'
    assert aggregate['worker_final']['error_class']=='ImportError'
    assert 'event_counts' in aggregate['worker_final']['observed_window_before_exception']
    assert nonzero_evidence['shadow']['child_cleanup_completed'] is True
elif case in ('root_abort','fixture_abort'):
    from scripts import rc6_issue465_stress as stress
    created=[]
    original=proof.NativeImportObserver
    def prepared(**kwargs):
        return dict(binding,parent_environment_before_fixtures={'test_scope':'CONTROLLED_SEAM_ONLY'},
          sys_path_before_fixtures=list(sys.path))
    def owned(*args,**kwargs):
        owned_observer=original(*args,**kwargs); created.append(owned_observer); return owned_observer
    proof.prepare_binding=prepared; proof.NativeImportObserver=owned
    target=private/'abort-case'; target.mkdir()
    if case=='root_abort': (target/'must-reject').write_text('not empty')
    else:
        stress.fixture_database=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('fixture fault'))
    try:
        stress.run_stress(target,canonical_runtime=True,source_provenance={'test_scope':'CONTROLLED_SEAM_ONLY'})
    except (ValueError,RuntimeError): pass
    else: raise AssertionError('abort not raised')
    assert len(created)==1 and created[0].active is False
    prior=dict(created[0].counts)
    exec(compile('VALUE=3','<string>','exec'),{})
    assert created[0].counts==prior
    next_observer=original(binding,role='AFTER_ABORT')
    exec(compile('VALUE=4','<string>','exec'),{})
    assert next_observer.finish()['event_counts']['opaque_exec']>=1
result=observer.finish()
result.update(test_case=case,test_binding_scope=binding['test_binding_scope'],
  child_pid=os.getpid(),initial_native_pid=initial['native_pid'])
if case=='nonzero_child':
    result['nonzero_child_evidence']=nonzero_evidence
    result['nonzero_child_control_scope']='ACTUAL_FORK_CHILD_OBSERVER_AND_PRIVATE_SQLITE_WITH_WORK_FAILURE_AND_EXIT_STUB_NOT_CANONICAL_SPAWN_BUSINESS_ACCEPTANCE'
print(json.dumps(result,sort_keys=True))
'''


def _actual_control(tmp_path, case):
    environment={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
    environment.pop('PYTHONPATH',None)
    completed=subprocess.run([sys.executable,'-I','-B','-c',SCRIPT,str(ROOT),str(tmp_path),case],
        cwd=ROOT,env=environment,capture_output=True,text=True,timeout=15,check=True)
    return json.loads(completed.stdout)


def test_actual_retained_pinned_module_origin_is_reported(tmp_path):
    result=_actual_control(tmp_path,'retained')
    rows=result['boundary_after']['modules']['scripts.rc6_sqlite_scratch_guard']
    assert rows[0]['origin']=='PINNED_SOURCE'
    assert rows[0]['relative_path']=='scripts/rc6_sqlite_scratch_guard.py'
    assert result['native_pid']==result['child_pid']==result['initial_native_pid']
    assert result['boundary_after']['scope'].startswith('RETAINED_MODULE_LOCATIONS')
    assert result['event_counts']['import']>0 and result['event_counts']['exec']>0


def test_actual_exec_of_alien_module_deleted_after_import_remains_blocked(tmp_path):
    result=_actual_control(tmp_path,'alien_deleted')
    assert 'rc6_deleted_alien_import' not in result['boundary_after']['modules']
    assert result['status']=='BLOCKED_ALIEN_OR_UNPINNED'
    assert result['transient_closure_verified'] is False
    assert any(row.get('filename')==str(tmp_path/'alien_import.py') for row in result['invalid'])


def test_actual_opaque_exec_is_unverified_without_generic_allowlist(tmp_path):
    result=_actual_control(tmp_path,'opaque')
    assert result['event_counts']['opaque_exec']>=1
    assert result['status']=='UNVERIFIED_LIMITS' and result['transient_closure_verified'] is False


def test_unresolved_filename_none_is_not_treated_as_a_closed_import(tmp_path):
    result=_actual_control(tmp_path,'none_unresolved')
    assert 'rc6_missing_observed_module' in result['filename_none_unresolved']
    assert result['transient_closure_verified'] is False


def test_bounded_audit_overflow_keeps_complete_counter_and_refuses_closure(tmp_path):
    result=_actual_control(tmp_path,'overflow')
    assert result['event_counts']['import']>=2049
    assert result['event_counts']['overflow']>0
    assert len(result['event_records'])<=result['record_limit']==2048
    assert result['transient_closure_verified'] is False


def test_actual_audit_hook_deactivation_does_not_contaminate_next_observer(tmp_path):
    result=_actual_control(tmp_path,'deactivation')
    assert result['event_counts']['opaque_exec']==0


def test_snapshot_failure_unconditionally_deactivates_owned_hook(tmp_path):
    _actual_control(tmp_path,'snapshot_failure')


@pytest.mark.parametrize('case',['root_abort','fixture_abort'])
def test_stress_early_failure_deactivates_hook_before_next_observer(tmp_path,case):
    _actual_control(tmp_path,case)


@pytest.mark.parametrize('case',['child_initial_abort','child_queue_abort'])
def test_child_startup_base_exception_deactivates_owned_hook(tmp_path,case):
    _actual_control(tmp_path,case)


def test_nonzero_actual_child_preserves_initial_and_failure_proof_in_typed_evidence(tmp_path):
    result=_actual_control(tmp_path,'nonzero_child')
    evidence=result['nonzero_child_evidence']
    assert evidence['import_provenance']['worker_exitcode']==1
    assert evidence['import_provenance']['worker_boundary_before']['status']=='IN_PROGRESS_NOT_CLOSED'
    assert evidence['import_provenance']['worker_final']['status']=='UNVERIFIED_WORKER_EXCEPTION'
    assert evidence['business_resource_complete'] is False and evidence['import_proof_complete'] is False
    assert evidence['shadow']['cycle_completion'] is False
    assert 'ACTUAL_FORK_CHILD' in result['nonzero_child_control_scope']


def test_partial_source_cli_is_rejected_before_fixture_or_business_import(tmp_path):
    completed=subprocess.run([sys.executable,'-I','-B',str(ROOT/'scripts/rc6_issue465_stress.py'),
        '--root',str(tmp_path/'must-stay-absent'),'--out',str(tmp_path/'result.json'),
        '--canonical-runtime','--source-sha','a'*40],cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert completed.returncode==2
    assert 'all five full source-binding arguments' in completed.stderr
    assert not (tmp_path/'must-stay-absent').exists()


def test_wrong_source_pin_rejects_before_environment_or_fixture(tmp_path,monkeypatch):
    from scripts import rc6_native_import_provenance as proof
    index=tmp_path/'source.index.json'
    index.write_text(json.dumps({'schema':'rc6.complete-archive-source-pin.v1','source_sha':'e'*40,
        'source_tree':'b'*40,'overlay_count':0,'files':{},'modes':{},'blob_ids':{}}))
    monkeypatch.setattr(proof,'environment_qualification',lambda *a: pytest.fail('environment reached after wrong pin'))
    with pytest.raises(ValueError,match='FULL_SOURCE_BINDING_REQUIRED'):
        proof.prepare_binding(source_root=ROOT,source_repo=ROOT,source_sha='a'*40,source_tree='b'*40,source_index=index)
    assert not (tmp_path/'data').exists()


def test_wrong_actual_parent_pid_reports_incomplete_before_product_startup(tmp_path):
    from scripts import rc6_issue465_stress as stress
    class Signal:
        def __init__(self): self.value=False
        def set(self): self.value=True
    class Queue:
        def __init__(self): self.messages=[]
        def put(self,message): self.messages.append(message)
    started,release,queue=Signal(),Signal(),Queue()
    binding={'prepared_by_pid':-1,'source_root':str(ROOT),'source_sha':'a'*40,
             'source_tree':'b'*40,'source_index_sha256':'c'*64}
    stress._shadow_child(str(tmp_path/'absent.db'),str(tmp_path/'absent-output'),started,release,queue,
        False,128*1024**2,True,None,binding)
    assert started.value and len(queue.messages)==1
    result=queue.messages[0]
    assert result['_probe_event']=='FINAL' and result['cycle_completion'] is False
    assert result['import_provenance']['status']=='UNVERIFIED_STARTUP'
    assert result['import_provenance']['native_pid']==os.getpid()
    assert result['import_provenance']['transient_closure_verified'] is False
    assert not (tmp_path/'absent.db').exists() and not (tmp_path/'absent-output').exists()


class _ActualSubprocessView:
    """Actual subprocess state adapted to the observer, not a fake RSS value."""
    def __init__(self,process): self.process,self.pid=process,process.pid
    def is_alive(self): return self.process.poll() is None
    @property
    def exitcode(self): return self.process.returncode


def test_late_proof_allocation_is_covered_by_actual_reaped_children_peak():
    from scripts import rc6_issue465_stress as stress
    late = (
        "import json,os,resource,sys\n"
        # Linux may retain the pre-exec parent's high-water mark. Fork only
        # after entering this small fresh interpreter to give the allocation
        # witness a baseline from its actual current address space. The outer
        # process reaps it; the observed RUSAGE_CHILDREN maximum is deliberately
        # a descendants-inclusive aggregate, never worker-only wait4.
        "allocation_pid=os.fork()\n"
        "if allocation_pid:\n"
        "    waited,status=os.waitpid(allocation_pid,0)\n"
        "    assert waited==allocation_pid\n"
        "    raise SystemExit(os.waitstatus_to_exitcode(status))\n"
        "before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024\n"
        "proof_payload=bytearray(96*1024**2)\n"
        "after=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024\n"
        "print(json.dumps({'reported_before_proof':before,'after_proof':after,\n"
        "    'allocation_pid':os.getpid(),'supervised_parent_pid':os.getppid(),\n"
        "    'scope':'ACTUAL_POSTEXEC_FORK_LATE96MIB_ALLOCATION_NOT_CANONICAL_WORKER'}),flush=True)\n"
        "os._exit(0)\n"
    )
    process=subprocess.Popen([sys.executable,'-I','-B','-c',late],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        stdout,stderr=process.communicate(timeout=15)
        assert process.returncode==0,stderr
        peaks=json.loads(stdout)
        assert peaks['supervised_parent_pid']==process.pid and peaks['allocation_pid']!=process.pid
        assert peaks['scope']=='ACTUAL_POSTEXEC_FORK_LATE96MIB_ALLOCATION_NOT_CANONICAL_WORKER'
        assert peaks['after_proof']>peaks['reported_before_proof']+64*1024**2
        result=stress._reaped_child_lifetime_rss(_ActualSubprocessView(process))
        assert result['worker_reaped_before_observation'] is True
        assert result['status']=='VERIFIED_REAPED_CHILDREN_MAXIMUM'
        assert result['peak_rss_bytes']>=peaks['after_proof']
        assert result['isolated_worker_wait4'] is False
        assert result['scope']=='PARENT_RUSAGE_CHILDREN_MAXIMUM_OF_ALL_REAPED_CHILDREN_INCLUDING_GIT_PREFLIGHT'
    finally:
        if process.poll() is None:
            process.kill(); process.communicate(timeout=5)


def test_unreaped_child_does_not_claim_a_lifetime_rss(monkeypatch):
    from scripts import rc6_issue465_stress as stress
    process=subprocess.Popen([sys.executable,'-I','-B','-c',"import time; time.sleep(5)"])
    try:
        monkeypatch.setattr(stress.resource,'getrusage',lambda *a: pytest.fail('unreaped child is absent from RUSAGE_CHILDREN'))
        result=stress._reaped_child_lifetime_rss(_ActualSubprocessView(process))
        assert result['status']=='UNVERIFIED_UNREAPED'
        assert result['worker_reaped_before_observation'] is False
        assert result['peak_rss_bytes'] is None
    finally:
        if process.poll() is None: process.kill()
        process.communicate(timeout=5)


def test_callback_source_has_no_hash_io_profile_or_gc_operation():
    # This is a declarative instrumentation contract, not a native import guard.
    path=ROOT/'scripts/rc6_native_import_provenance.py'
    tree=ast.parse(path.read_bytes())
    observer=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='NativeImportObserver')
    callback=next(node for node in observer.body if isinstance(node,ast.FunctionDef) and node.name=='audit')
    called={ast.unparse(node.func) for node in ast.walk(callback) if isinstance(node,ast.Call)}
    assert not any(value in called for value in ('open','os.open','os.read','sys.setprofile','sys.settrace','gc.collect','hashlib.sha256'))
