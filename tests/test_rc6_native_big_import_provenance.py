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


def _runtime_control_child(root, private, case):
    """Physical Source main and actual interpreter events; no market/runtime.

    This observer-unit binding is intentionally not raw-Git/frozen157 authority.
    The enclosing governed focal must establish that authority before fixtures.
    """
    import collections
    import dataclasses
    import importlib
    import stat
    import types
    import typing
    sys.path.insert(0, str(root))
    from scripts import rc6_native_import_provenance as proof
    private.mkdir(exist_ok=True)
    files = {}
    for path in root.rglob('*.py'):
        if path.is_file() and not path.is_symlink():
            body = path.read_bytes()
            files[path.relative_to(root).as_posix()] = {
                'sha256':hashlib.sha256(body).hexdigest(),
                'mode':'100'+format(stat.S_IMODE(path.stat().st_mode), '03o'),
                'blob_id':hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()}
    binding = {'source_root':str(root),'source_sha':'a'*40,'source_tree':'b'*40,
               'source_index_sha256':'c'*64,'files':files,'prepared_by_pid':os.getpid(),
               'test_binding_scope':'PHYSICAL_OBSERVER_UNIT_CONTROL_NOT_RAW_GIT_QUALIFICATION'}
    before_find, before_load = proof._bootstrap._find_and_load, proof._bootstrap._load_unlocked
    control = {'case':case,'provider_requests':0,'real_orders_sent':0,'business_runtime_executed':False}
    installation_cases = ('audit_hook_veto','audit_hook_duplicate','audit_hook_error')
    if case in installation_cases:
        pending = [True]
        def prior_hook(event, arguments):
            if pending[0] and case == 'audit_hook_veto' and event == 'sys.addaudithook':
                pending[0] = False
                raise RuntimeError('controlled silent installation veto')
            if pending[0] and event == proof._INSTALLATION_EVENT:
                pending[0] = False
                if case == 'audit_hook_duplicate':
                    sys.audit(event, *arguments)
                elif case == 'audit_hook_error':
                    raise KeyboardInterrupt('controlled sentinel interruption')
        sys.addaudithook(prior_hook)
        rejected = proof.NativeImportObserver.__new__(proof.NativeImportObserver)
        try:
            rejected.__init__(binding, role='REJECTED_INSTALLATION_CONTROL')
        except (ValueError, KeyboardInterrupt) as error:
            control['installation_error_class'] = type(error).__name__
            if case == 'audit_hook_error':
                assert type(error) is KeyboardInterrupt
            else:
                assert type(error) is ValueError and str(error) == 'IMPORT_PROVENANCE_AUDIT_HOOK_INSTALLATION_UNVERIFIED'
        else:
            raise AssertionError('installation without unique sentinel was accepted')
        assert rejected.active is rejected.installing is rejected.installation_verified is False
        assert rejected._installation_nonce is None
        assert proof._bootstrap._find_and_load is before_find and proof._bootstrap._load_unlocked is before_load
        assert not any(owner is rejected for owner in proof._PROTOCOL_OWNERS)
        assert rejected.counts == {key:0 for key in rejected.counts}
        control['rejected_installation_receptions'] = rejected.installation_receptions
        control['rejected_observer_inactive_and_protocol_restored'] = True
    observer = proof.NativeImportObserver(binding, role='RUNTIME_FACTORY_CONTROL')
    before = observer.initial_receipt()
    if case in installation_cases:
        assert before['audit_hook_installation']['verified'] is True
        assert before['audit_hook_installation']['receptions'] == 1
    elif case == 'typing_aliases':
        control['actual_aliases'] = [name for name in ('typing.io','typing.re') if name in sys.modules]
        for name in control['actual_aliases']:
            alias = sys.modules[name]
            assert alias is vars(typing)[name.removeprefix('typing.')]
            assert type(alias) is vars(typing)['_DeprecatedType']
    elif case == 'false_typing_alias':
        # Use the real metaclass and matching public names; only exact alias
        # identity in the real parent module can reject this lookalike.
        if '_DeprecatedType' in vars(typing):
            false_alias = vars(typing)['_DeprecatedType']('typing.io', (), {'__module__':'typing','__qualname__':'io'})
        else:
            false_alias = type('typing.io', (), {'__module__':'typing','__qualname__':'io'})
        sys.modules['typing.io'] = false_alias
    elif case in ('namedtuple','dataclass','lookalike_factory','factory_text_bound'):
        if case == 'namedtuple':
            record = collections.namedtuple('ActualRuntimeRecord', ('left','right'))(3, 4)
            assert record.left == 3 and record.right == 4
        elif case == 'dataclass':
            @dataclasses.dataclass(frozen=True)
            class ActualRuntimeRecord:
                left: int
                right: int
            assert ActualRuntimeRecord(3, 4).right == 4
        elif case == 'lookalike_factory':
            false_factory = types.FunctionType(collections.namedtuple.__code__, dict(vars(collections)))
            assert false_factory('LookalikeRuntimeRecord', ('left',))(3).left == 3
        else:
            # Actual valid Python factory input exceeds the existing text bound.
            # Generation still runs; provenance remains incomplete without a cap raise.
            record = collections.namedtuple('LargeRuntimeRecord', ['field_'+str(n) for n in range(500)])
            assert len(record._fields) == 500
    elif case in ('real_frozen','fake_frozen'):
        if case == 'real_frozen':
            import _imp
            code = _imp.get_frozen_object('runpy')
            exec(code, {'__name__':'rc6_control_frozen_runpy','__package__':None})
        else:
            exec(compile('INJECTED=True', '<frozen runpy>', 'exec'), {})
    elif case in ('optional_missing','missing_repeated','fabricated_missing'):
        name = 'rc6_runtime_guard_optional_package_absent'
        repetitions = 100 if case == 'missing_repeated' else 1
        for _ in range(repetitions):
            try:
                __import__(name)
            except ModuleNotFoundError as error:
                assert error.name == name
            else:
                raise AssertionError('missing package unexpectedly available')
        if case == 'fabricated_missing':
            sys.audit('import', 'rc6_runtime_guard_fabricated_missing', None, None, None, None)
    elif case == 'constructed_missing':
        name = 'rc6_runtime_guard_constructed_missing'
        # The same name first has a genuine core failure. A later user-finder
        # exception must remain a distinct unqualified outcome, not merge into it.
        try:
            __import__(name)
        except ModuleNotFoundError as error:
            assert error.name == name
        else:
            raise AssertionError('missing package unexpectedly available')
        class FalseMissingFinder:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == name:
                    raise ModuleNotFoundError('constructed finder exception', name=fullname)
                return None
        finder = FalseMissingFinder()
        sys.meta_path.insert(0, finder)
        try:
            __import__(name)
        except ModuleNotFoundError as error:
            assert error.name == name
        else:
            raise AssertionError('constructed failure lost')
        finally:
            sys.meta_path.remove(finder)
    elif case in ('transient_pinned','transient_alien','loader_abort'):
        if case == 'transient_pinned':
            name = 'scripts.rc6_sqlite_scratch_guard'
        else:
            name = 'rc6_runtime_guard_private_module'
            body = 'VALUE=17\n' if case == 'transient_alien' else 'raise SystemExit("controlled loader abort")\n'
            (private/(name+'.py')).write_text(body)
            sys.path.insert(0, str(private))
        try:
            imported = __import__(name, fromlist=['_rc6_control'])
        except SystemExit:
            assert case == 'loader_abort'
        else:
            assert imported.__name__ == name and case != 'loader_abort'
            del sys.modules[name]
    elif case in ('cython_extension','false_cython_after','unknown_runtime_module'):
        if case == 'unknown_runtime_module':
            sys.modules['rc6_unknown_extension_runtime'] = types.ModuleType('rc6_unknown_extension_runtime')
        else:
            actual = importlib.import_module('charset_normalizer.cd')
            assert type(actual.__loader__) is proof._EXTENSION_LOADER
            assert actual.__spec__.origin == actual.__file__
            control['actual_extension_path'] = actual.__file__
            control['actual_cython_modules'] = [name for name in sys.modules
                                               if name == 'cython_runtime' or name.startswith('_cython_')]
            assert 'cython_runtime' in control['actual_cython_modules']
            if case == 'false_cython_after':
                false_module = types.ModuleType('cython_runtime')
                false_module.line_trace = False
                sys.modules['cython_runtime'] = false_module
    else:
        raise AssertionError('unknown control')
    result = observer.finish()
    assert observer.active is False
    assert proof._bootstrap._find_and_load is before_find and proof._bootstrap._load_unlocked is before_load
    assert not observer.opaque_references and not observer.synthetic_modules
    result.update(control=control,test_binding_scope=binding['test_binding_scope'],
                  native_child_pid=os.getpid(),initial_native_pid=before['native_pid'],
                  original_import_machinery_restored=True)
    print(json.dumps(result, sort_keys=True))


def _physical_runtime_control(tmp_path, case):
    environment = {**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
    environment.pop('PYTHONPATH', None)
    completed = subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),
        '--runtime-provenance-control',str(ROOT),str(tmp_path),case], cwd=tmp_path,
        env=environment,capture_output=True,text=True,timeout=20,check=True)
    result = json.loads(completed.stdout)
    assert result['native_pid'] == result['native_child_pid'] == result['initial_native_pid']
    assert result['original_import_machinery_restored'] is True
    assert result['shared_evidence_record_count'] <= result['record_limit'] == 2048
    assert result['test_binding_scope'] == 'PHYSICAL_OBSERVER_UNIT_CONTROL_NOT_RAW_GIT_QUALIFICATION'
    return result


def test_exact_typing_public_aliases_are_classified_without_pretending_module_type(tmp_path):
    result = _physical_runtime_control(tmp_path, 'typing_aliases')
    for name in result['control']['actual_aliases']:
        row = result['boundary_before']['modules'][name][0]
        assert row['origin'] == 'NONMODULE_PUBLIC_TYPING_ALIAS' and row['parent_module'] == 'typing'
        assert row['scope'].startswith('EXACT_RETAINED_PARENT_ALIAS_TYPE_RELATIONSHIP')
    assert not any(row['module'] in ('typing.io','typing.re') for row in result['boundary_before']['unresolved'])


@pytest.mark.parametrize('case,receptions,error_class',[
    ('audit_hook_veto',0,'ValueError'),('audit_hook_duplicate',2,'ValueError'),
    ('audit_hook_error',0,'KeyboardInterrupt')])
def test_audit_hook_installation_requires_unique_owned_sentinel_and_restores_after_failure(tmp_path, case, receptions, error_class):
    result = _physical_runtime_control(tmp_path, case)
    assert result['control']['rejected_installation_receptions'] == receptions
    assert result['control']['installation_error_class'] == error_class
    assert result['control']['rejected_observer_inactive_and_protocol_restored'] is True
    assert result['audit_hook_installation']['verified'] is True and result['audit_hook_installation']['receptions'] == 1
    assert result['event_counts'] == {key:0 for key in result['event_counts']}


def test_false_typing_alias_with_real_metaclass_and_matching_name_stays_unverified(tmp_path):
    result = _physical_runtime_control(tmp_path, 'false_typing_alias')
    assert any(row['module'] == 'typing.io' for row in result['boundary_after']['unresolved'])
    assert result['transient_closure_verified'] is False


@pytest.mark.parametrize('case,factory',[('namedtuple','collections.namedtuple'),('dataclass','dataclasses._create_fn')])
def test_real_language_factory_exec_has_exact_code_and_callsite_evidence(tmp_path, case, factory):
    result = _physical_runtime_control(tmp_path, case)
    rows = [row['runtime_code_provenance'] for row in result['event_records'] if row.get('runtime_code_provenance')]
    assert any(row['status'] == 'QUALIFIED_RUNTIME_FACTORY_CODE' and row.get('factory') == factory for row in rows)
    assert all(row['status'] != 'UNVERIFIED' for row in rows)
    assert result['event_counts']['opaque_exec'] > 0 and result['opaque_exec_unresolved_records'] == 0


def test_cloned_factory_with_same_code_but_foreign_globals_is_rejected(tmp_path):
    result = _physical_runtime_control(tmp_path, 'lookalike_factory')
    assert result['opaque_exec_unresolved_records'] > 0 and result['transient_closure_verified'] is False


def test_valid_language_generation_over_text_bound_does_not_raise_provenance_cap(tmp_path):
    result = _physical_runtime_control(tmp_path, 'factory_text_bound')
    assert result['event_counts']['overflow'] > 0 and result['transient_closure_verified'] is False


@pytest.mark.parametrize('case,qualified',[('real_frozen',True),('fake_frozen',False)])
def test_frozen_filename_requires_actual_interpreter_code_reference(tmp_path, case, qualified):
    result = _physical_runtime_control(tmp_path, case)
    rows = [row['runtime_code_provenance'] for row in result['event_records'] if row.get('runtime_code_provenance')]
    if qualified:
        assert any(row['status'] == 'QUALIFIED_FROZEN_CODE' and row.get('interpreter_frozen_name') == 'runpy' for row in rows)
    else:
        assert result['opaque_exec_unresolved_records'] > 0 and result['transient_closure_verified'] is False


@pytest.mark.parametrize('case,repetitions',[('optional_missing',1),('missing_repeated',100)])
def test_actual_optional_missing_outcome_is_bounded_and_distinct_from_final_absence(tmp_path, case, repetitions):
    result = _physical_runtime_control(tmp_path, case)
    name = 'rc6_runtime_guard_optional_package_absent'
    row = next(row for row in result['filename_none_resolved_by_import_lifecycle'] if row['module'] == name)
    assert row['outcomes'][0]['basis'] == 'ACTUAL_MODULE_NOT_FOUND_BEFORE_ANY_SELECTED_LOADER'
    assert row['outcomes'][0]['error_name'] == name and row['outcomes'][0]['occurrences'] == repetitions
    assert name not in result['filename_none_unresolved'] and result['event_counts']['overflow'] == 0


def test_fabricated_missing_audit_event_has_no_actual_import_outcome(tmp_path):
    result = _physical_runtime_control(tmp_path, 'fabricated_missing')
    assert 'rc6_runtime_guard_fabricated_missing' in result['filename_none_unresolved']
    assert result['transient_closure_verified'] is False


def test_user_finder_constructed_module_not_found_is_not_core_optional_absence(tmp_path):
    result = _physical_runtime_control(tmp_path, 'constructed_missing')
    name = 'rc6_runtime_guard_constructed_missing'
    assert name in result['filename_none_unresolved']
    rows = [row for row in result['import_attempt_outcomes'] if row['module'] == name]
    assert {row['core_missing_raiser_verified'] for row in rows} == {False, True}
    assert all(row['outcome'] == 'MODULE_NOT_FOUND' for row in rows)
    assert result['transient_closure_verified'] is False


def test_actual_pinned_transient_module_is_joined_to_loader_and_execution_after_deletion(tmp_path):
    result = _physical_runtime_control(tmp_path, 'transient_pinned')
    name = 'scripts.rc6_sqlite_scratch_guard'
    assert name not in result['boundary_after']['modules']
    row = next(row for row in result['filename_none_resolved_by_import_lifecycle'] if row['module'] == name)
    selected = row['outcomes'][0]['selected_loaders'][0]
    assert selected['observed_origin']['origin'] == 'PINNED_SOURCE'
    assert selected['execution_event_basis'] == 'ACTUAL_MATCHING_IMPORT_OR_EXEC_EVENT'


@pytest.mark.parametrize('case',['transient_alien','loader_abort'])
def test_private_alien_loader_and_base_exception_preserve_evidence_and_restore_protocol(tmp_path, case):
    result = _physical_runtime_control(tmp_path, case)
    assert result['status'] == 'BLOCKED_ALIEN_OR_UNPINNED' and result['transient_closure_verified'] is False
    if case == 'loader_abort':
        assert any(row.get('error_class') == 'SystemExit' for row in result['selected_loader_outcomes'])
    assert result['original_import_machinery_restored'] is True


def test_actual_cython_extension_creation_has_exact_registry_parent_and_type_lineage(tmp_path):
    result = _physical_runtime_control(tmp_path, 'cython_extension')
    for name in result['control']['actual_cython_modules']:
        row = result['boundary_after']['modules'][name][0]
        assert row['origin'] == 'EXTENSION_CREATED_RUNTIME_MODULE'
        assert row['extension_module'] in ('charset_normalizer.cd','charset_normalizer.md')
        assert row['path'] == result['boundary_after']['modules'][row['extension_module']][0]['path']
        assert row['referenced_type_attributes'] or (name == 'cython_runtime' and row['runtime_shape_observed'])


@pytest.mark.parametrize('case,name',[('false_cython_after','cython_runtime'),('unknown_runtime_module','rc6_unknown_extension_runtime')])
def test_replaced_or_unknown_native_runtime_module_has_no_name_based_exemption(tmp_path, case, name):
    result = _physical_runtime_control(tmp_path, case)
    assert any(row['module'] == name for row in result['boundary_after']['unresolved'])
    assert result['transient_closure_verified'] is False


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[1] != '--runtime-provenance-control':
        raise SystemExit('This entrypoint only runs explicit private observer controls.')
    _runtime_control_child(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
